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

Shape 2 is now DIFFERENTIAL: render the same topology with a family and without it, and compare.
If adding a family does not change what the module renders, the module is ignoring that family.
No device vocabulary is involved, so this is correct for any device.

Usage: af_sweep.py <device> [module ...]
"""
import os, re, shutil, subprocess, sys, tempfile

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
OPENER   = re.compile(r"^(\s*)(instance \S+|area \S+|network-instance \S+)\s*$")
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
      return [f"RENDER FAILED: {lb[0][:70]}"]

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

    # Shape 2 -- differential. Drop each family the node has and re-render; if the module's
    # output is unchanged, that family is being ignored entirely.
    for af in ("ipv4","ipv6"):
      if af not in link_af:
        continue
      tmp2 = tempfile.mkdtemp(prefix="afs2.")
      try:
        # Drop the family from the loopback AS WELL as the link. Modules do not all consume the
        # same addressing: OSPF follows interfaces, but BGP address families follow the loopback
        # (that is where the router-id and iBGP endpoints come from). Dropping it from the link
        # alone leaves BGP unchanged for a legitimate reason and reports a false positive on
        # exactly the mixed-AF nodes this sweep exists to cover.
        other    = [x for x in link_af if x != af]
        other_lo = [x for x in lo_af   if x != af]
        if not other or not other_lo:
          continue                                  # cannot drop the only family
        cfgs2, _ = render(name, other_lo, other, tmp2)
        if cfgs2 is None:
          continue
        for mod in MODULES:
          if cfgs.get(mod,"") == cfgs2.get(mod,"") and cfgs.get(mod,"").strip():
            notes.append(f"{mod}: dropping {af} changes nothing -- family not rendered")
          if not cfgs.get(mod,"").strip():
            notes.append(f"{mod}: rendered NOTHING")
      finally:
        os.chdir("/"); shutil.rmtree(tmp2, ignore_errors=True)
  finally:
    os.chdir("/"); shutil.rmtree(tmp, ignore_errors=True)
  return notes


def main():
  total = 0
  for name, lo_af, link_af in CASES:
    notes = one_case(name, lo_af, link_af)
    total += len(notes)
    print(f"  {name:16} {'OK' if not notes else '; '.join(sorted(set(notes)))}")
  print(f"\n  device={DEVICE} modules={','.join(MODULES)}  findings={total}")
  return 1 if total else 0


if __name__ == "__main__":
  sys.exit(main())
