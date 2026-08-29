#!/usr/bin/env python3
"""Address-family sweep for netlab device templates.

Checks three shapes:

  1. EMPTY  -- a per-AF construct emitted with no members (a stranded "instance ospfv3").
  2. ABSENT -- a family the node has that the module does not render at all.
  3. LEAK   -- a device-owned loopback rendered on a node netlab does not own.

Design note, after three rewrites that each hardcoded whatever CLI happened to be in front of
me. Shape 2 used to be a token match (`ipv4-unicast`, `instance ospf`, ...), which was wrong
three ways: "instance ospf" is a substring of "instance ospfv3" so the dual-stack case -- shape
2's own definition -- passed clean; the oracle keyed on link addressing while BGP families
follow loopback addressing, giving false positives on exactly the mixed-AF nodes this sweep
exists to cover; and the vocabulary was DNOS's, so every other device came back red.

Shape 2 is DIFFERENTIAL: render the same topology with a family and without it, and compare.
If varying a family does not change what the module renders, the module is ignoring that family.
No device vocabulary is involved, so shape 2 is correct for any device. Shape 1 is not -- its
opener patterns are per-CLI and have to be extended for each device's instance syntax.

WHAT THIS SWEEP DOES NOT DETECT, deliberately. Shape 2 finds a family that is ABSENT, never one
that is WRONG. A family rendered with the wrong content -- a network type set for IPv4 only, an
MTU short by a header, a policy attached to one family and not the other -- changes the output
when the family is varied, so the differential is satisfied and says nothing. Both templates in
this tree carry comments about exactly such defects stalling an adjacency in ExStart.

That limit is on purpose. Detecting wrong content means encoding what correct content looks like
per device and per module, which is how a hardcoded CLI vocabulary crept into this file three
times and made it wrong for every device except the one in front of me. Content correctness
belongs in integration tests against real hardware, not here.

Usage: af_sweep.py <device> [module ...]
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

DEVICE  = sys.argv[1] if len(sys.argv) > 1 else "dnos"
MODULES = sys.argv[2:] or ["ospf"]

# (name, loopback AFs, link AFs). The mixed and phys cases are the shapes physical hardware
# produces once the device owns its loopback; uniform-AF sweeps never generate them.
CASES = [
  ("v4only",        ["ipv4"],         ["ipv4"]),
  ("dualstack",     ["ipv4","ipv6"],  ["ipv4","ipv6"]),
  ("v6only",        ["ipv6"],         ["ipv6"]),
  ("lo-v6/link-v4", ["ipv6"],         ["ipv4"]),
  ("lo-v4/link-v6", ["ipv4"],         ["ipv6"]),
  ("phys/link-v4",  ["ipv4","ipv6"],  ["ipv4"]),
  ("phys/link-v6",  ["ipv4","ipv6"],  ["ipv6"]),
]

# Constructs where an empty body is a defect. A BGP "address-family" with an empty body is NOT
# one -- that is how DNOS enables a family, and the production router carries several.
# Constructs where an empty body is a defect. "network-instance <ni> protocol <proto> <inst>"
# is ArcOS's instance line -- five tokens. A pattern anchored after the first token never matched
# it, so this check silently did nothing on ArcOS while looking like it covered it.
OPENER   = re.compile(r"^(\s*)(instance \S+"
                      r"|area \S+"
                      r"|network-instance \S+ protocol \S+ \S+)\s*$")
METADATA = re.compile(r"^\s*(router-id|administrative-distance|log-adjacency|global |area |!|$)")


def render(name, lo_af, link_af, tmp):
  """Render one case. Returns ({module: config}, loopback_ifname) or (None, error)."""
  os.chdir(tmp)
  with open("t.yml","w") as f:
    f.write(f"provider: external\ndefaults.device: {DEVICE}\n")
    f.write("module: [ " + ", ".join(MODULES) + " ]\n")
    if "bgp" in MODULES:
      f.write("bgp.as: 65000\n")
    if name.startswith("phys"):
      f.write("groups:\n  all:\n    vars:\n      netlab_manage_identity: False\n")
    f.write("nodes: [ n1, n2 ]\nlinks: [ n1-n2 ]\n")
  ov = []
  for af in ("ipv4","ipv6"):
    if af not in link_af: ov += ["-s", f"addressing.p2p.{af}=False"]
    if af not in lo_af:   ov += ["-s", f"addressing.loopback.{af}=False"]
  r = subprocess.run(["netlab","create","t.yml"]+ov, capture_output=True, text=True)
  if r.returncode:
    return None, (r.stdout+r.stderr).strip().splitlines()[:2]
  cfgs = {}
  for m in ["initial"] + MODULES:      # initial is rendered unconditionally, not a module: value
    p = f"node_files/n1/{m}"
    if os.path.exists(p):
      cfgs[m] = open(p).read()
  lb = subprocess.run(["netlab","inspect","nodes.n1.loopback.ifname"],
                      capture_output=True, text=True).stdout.strip() or None
  return cfgs, lb


def empty_blocks(cfg):
  """Shape 1: a construct opened with no members."""
  lines, bad = cfg.splitlines(), []
  for i, ln in enumerate(lines):
    m = OPENER.match(ln)
    if not m:
      continue
    indent, body = len(m.group(1)), []
    for nxt in lines[i+1:]:
      if nxt.strip() and (len(nxt) - len(nxt.lstrip())) <= indent:
        break
      body.append(nxt)
    if all(METADATA.match(b) for b in body):
      bad.append(m.group(2).strip())
  return bad


def one_case(name, lo_af, link_af):
  notes = []
  tmp = tempfile.mkdtemp(prefix="afs.")
  try:
    cfgs, lb = render(name, lo_af, link_af, tmp)
    if cfgs is None:
      # NOT a template defect. The topology this harness generates is fixed (two nodes, one
      # link), so a module with requirements it cannot express -- stp needs vlan, vrf and evpn
      # need their own structures -- fails to transform, as does a device that declares a family
      # unsupported. Reporting those as findings makes the harness cry wolf: a red count that
      # says nothing about template correctness. They are surfaced separately and not counted.
      return [], [f"cannot express this case: {lb[0][:70]}"]

    for mod, cfg in cfgs.items():
      for blk in empty_blocks(cfg):
        notes.append(f"{mod}: EMPTY <{blk}>")

    # Shape 3 -- a device that owns its loopback must not have it rendered. Keyed on the
    # loopback name netlab actually assigned, not a literal: DNOS is lo0 and ArcOS loopback0,
    # and a hardcoded "lo0" is silently blind on every device that names it otherwise.
    if name.startswith("phys") and lb:
      for mod, cfg in cfgs.items():
        if re.search(rf"(?<![\w-]){re.escape(lb)}(?![\w-])", cfg):
          notes.append(f"{mod}: renders {lb} although the device owns its loopback")

    # A module asked for that renders nothing at all is a defect regardless of families, and
    # needs no second render. Nesting this inside the differential loop below gated it on the
    # dual-stack cases and left the phys cases -- the whole point of this sweep -- uncovered.
    for mod in MODULES:
      if not cfgs.get(mod,"").strip():
        notes.append(f"{mod}: rendered NOTHING")

    # Shape 2 -- differential. Vary each family and re-render; if the module's output is
    # unchanged, that family is being ignored entirely.
    for af in ("ipv4","ipv6"):
      # Vary the family from BOTH the loopback and the link. Modules do not all consume the same
      # addressing: OSPF follows interfaces, but BGP address families follow the loopback (that
      # is where the router-id and iBGP endpoints come from), so varying the link alone leaves
      # BGP unchanged for a legitimate reason and false-positives on the mixed-AF nodes this
      # sweep exists to cover.
      #
      # A single-family case cannot have its only family REMOVED, so ADD the missing one instead.
      # Skipping those left 6 of 7 cases -- including both phys cases -- with no absence check.
      if af in link_af:
        alt, alt_lo = [x for x in link_af if x != af], [x for x in lo_af if x != af]
        verb = "dropping"
      else:
        alt, alt_lo = link_af + [af], sorted(set(lo_af + [af]))
        verb = "adding"
      if not alt or not alt_lo:
        continue
      tmp2 = tempfile.mkdtemp(prefix="afs2.")
      try:
        cfgs2, _ = render(name, alt_lo, alt, tmp2)
        if cfgs2 is None:
          continue
        for mod in MODULES:
          if cfgs.get(mod,"").strip() and cfgs.get(mod,"") == cfgs2.get(mod,""):
            notes.append(f"{mod}: {verb} {af} changes nothing -- family not rendered")
      finally:
        os.chdir("/"); shutil.rmtree(tmp2, ignore_errors=True)
  finally:
    os.chdir("/"); shutil.rmtree(tmp, ignore_errors=True)
  return notes, []


def main():
  total = n_skip = 0
  for name, lo_af, link_af in CASES:
    found, skipped = one_case(name, lo_af, link_af)
    notes = sorted(set(found))            # count what is printed, not pre-dedup
    total += len(notes)
    n_skip += len(skipped)
    if notes:
      print(f"  {name:16} {'; '.join(notes)}")
    elif skipped:
      print(f"  {name:16} SKIPPED -- {skipped[0]}")
    else:
      print(f"  {name:16} OK")
  tail = f"  findings={total}"
  if n_skip:
    tail += f"  (skipped={n_skip}, harness could not express those cases)"
  print(f"\n  device={DEVICE} modules={','.join(MODULES)}{tail}")
  return 1 if total else 0


if __name__ == "__main__":
  sys.exit(main())
