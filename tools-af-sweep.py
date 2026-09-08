#!/usr/bin/env python3
"""Address-family sweep for netlab device templates.

Checks four shapes:

  1. EMPTY  -- a per-AF construct emitted with no members (a stranded "instance ospfv3").
  2. ABSENT -- a family the node has that the module does not render at all.
  3. LEAK   -- a device-owned loopback rendered on a node netlab does not own.
  4. MISMATCH -- a member listed under a per-AF construct that does not have that family.

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

Shape 4 reaches only a CLI whose per-AF construct matches OPENER above and whose opener names a
family -- ArcOS and DNOS today. On a device whose OSPF config never spells a family into a
construct header, it silently checks nothing, so a clean run there is not evidence. That is the
same per-CLI limit shape 1 carries, stated here because a silent zero is easy to misread.

Shape 4 is the one shape that needs a node whose INTERFACES disagree with each other, which is
why the split cases exist. The defect it hunts is a filter keyed on the wrong family --
`if l.ipv4 is defined` inside the loop that builds the IPv6 area -- and on a uniform dual-stack
node it is invisible, because there every interface has both families and the wrong filter
selects exactly the right set. Give the node one v4-only and one v6-only interface and the v6
construct comes back listing the v4 interface. Shapes 1-3 all pass on that: the construct is not
empty, the family is not absent, and no loopback leaked. Measured on this tree.

WHAT THIS SWEEP STILL DOES NOT DETECT, deliberately. Shapes 2 and 4 find a family that is ABSENT
or a member that is in the WRONG construct. Neither finds a member that is present, correctly
placed, and carries wrong SETTINGS -- a network type set for IPv4 only, an MTU short by a header,
a policy attached to one family and not the other. Both templates in this tree carry comments
about exactly such defects stalling an adjacency in ExStart. Detecting those means encoding what
correct content looks like per device and per module, which is how a hardcoded CLI vocabulary
crept into this file three times; it belongs in integration tests against real hardware.

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

# (name, loopback AFs, link AFs, split). The mixed and phys cases are the shapes physical
# hardware produces once the device owns its loopback; uniform-AF sweeps never generate them.
#
# `split` is the last shape issue #73 asked for and the only one the rest of this table cannot
# express: the AF profile varies BETWEEN two interfaces of the same node, not just between the
# node's link and its loopback. Every other case here sets the families with a global
# `addressing.p2p.<af>=False`, which by construction gives every link the same profile -- so a
# construct that is correct per-node while being wrong per-interface (an area holding only the
# v4 half of a dual-stack node's interfaces, say) renders identically in all seven and is
# invisible. The split case writes its links explicitly instead, one family each.
CASES = [
  ("v4only",        ["ipv4"],         ["ipv4"],         False),
  ("dualstack",     ["ipv4","ipv6"],  ["ipv4","ipv6"],  False),
  ("v6only",        ["ipv6"],         ["ipv6"],         False),
  ("lo-v6/link-v4", ["ipv6"],         ["ipv4"],         False),
  ("lo-v4/link-v6", ["ipv4"],         ["ipv6"],         False),
  ("phys/link-v4",  ["ipv4","ipv6"],  ["ipv4"],         False),
  ("phys/link-v6",  ["ipv4","ipv6"],  ["ipv6"],         False),
  ("split-links",   ["ipv4","ipv6"],  ["ipv4","ipv6"],  True),
  ("split-phys",    ["ipv4","ipv6"],  ["ipv4","ipv6"],  True),
]

# Constructs where an empty body is a defect. A BGP "address-family" with an empty body is NOT
# one -- that is how DNOS enables a family, and the production router carries several.
# Constructs where an empty body is a defect. "network-instance <ni> protocol <proto> <inst>"
# is ArcOS's instance line -- five tokens. A pattern anchored after the first token never matched
# it, so this check silently did nothing on ArcOS while looking like it covered it.
# A construct opener. Per-CLI, as shape 1's docstring says. The bare-word alternative is what
# reaches DNOS: its OSPFv3 process is the single word "ospfv3" nested under "protocols", so with
# only the multi-token patterns here nothing opened a frame, the "area 0.0.0.0" beneath it had no
# parent to inherit a family from, and shape 4 skipped every member -- silently, on the very
# device whose defect motivated issue #73. Restricted to the protocol container words we render,
# not any bare word: "interface swp1" must stay a MEMBER, never an opener.
OPENER   = re.compile(r"^(\s*)(instance \S+"
                      r"|area \S+"
                      r"|network-instance \S+ protocol \S+ \S+"
                      r"|ospfv?3|ospf6|isis|bgp)\s*$")
METADATA = re.compile(r"^\s*(router-id|administrative-distance|log-adjacency|global |area |!|$)")

# Which family a construct's opener declares, for shape 4. Per-CLI by nature, exactly as the
# OPENER patterns above are: "ospfv3"/"OSPF3" is the v6 instance on DNOS/ArcOS, and an "OSPF p1"
# with no 3 in it is the v4 one. A construct whose opener says nothing about a family (a bare
# "area 0") inherits the family of the construct it sits inside, so it is resolved by nesting
# rather than guessed at here.
# Only patterns that can match a line OPENER already matched belong here -- opener_af() is never
# called on anything else, so an entry for a construct OPENER cannot enter (an `address-family
# ipv6` line, say) reads as coverage the tool does not have.
AF_OPENER = [
  (re.compile(r"(?i)^(ospf ?v?3|ospf6)$"),          "ipv6"),   # DNOS bare container word. Its
                                                               # v4 sibling is "instance ospf",
                                                               # matched below -- DNOS renders no
                                                               # bare "ospf" line to match here.
  (re.compile(r"(?i)\b(ospf ?v?3|ospf6)\b"),        "ipv6"),   # ArcOS OSPF3 / "instance ospfv3"
  (re.compile(r"(?i)\bprotocol\s+OSPF\s"),         "ipv4"),   # ArcOS "protocol OSPF p1"
  (re.compile(r"(?i)\binstance\s+ospf\b(?!v?3)"),  "ipv4"),
]
MEMBER_IF = re.compile(r"^\s*interface\s+(\S+)\s*$")


def opener_af(text):
  for rx, af in AF_OPENER:
    if rx.search(text):
      return af
  return None


def af_mismatches(cfg, if_afs):
  """Shape 4: an `interface X` member under a construct of a family X does not have.

  `if_afs` maps ifname -> set of families the node actually configured on it. An interface the
  map does not know (a loopback the device owns, a name the provider renamed) is skipped rather
  than reported: this shape exists to catch a wrong FILTER, and an unknown name is not evidence
  of one.
  """
  bad, stack = [], []            # stack of (indent, af-or-None, opener text, af_declared_here)
  for ln in cfg.splitlines():
    if not ln.strip():
      continue
    indent = len(ln) - len(ln.lstrip())
    while stack and indent <= stack[-1][0]:
      stack.pop()
    m = OPENER.match(ln)
    if m:
      text = m.group(2).strip()
      own = opener_af(text)
      af = own or (stack[-1][1] if stack else None)
      stack.append((indent, af, text, own is not None))
      continue
    mi = MEMBER_IF.match(ln)
    if not mi or not stack:
      continue
    af = stack[-1][1]
    ifname = mi.group(1)
    if af and ifname in if_afs and af not in if_afs[ifname]:
      # Name the construct that DECLARED the family, not the innermost frame that inherited it:
      # "listed under <network-instance default protocol OSPF3 p1>" points at the template branch
      # to fix, where "<area 0>" leaves the reader to work out which family's area it was.
      owner = next((t for _, _a, t, own in stack if own), stack[-1][2])
      bad.append(f"{ifname} has no {af} but is listed under <{owner}>")
  return bad


# The split case's links, as (peer, family) pairs. The differential rewrites this rather than
# touching the global addressing pools, because the split case pins its families in the topology
# text where an `addressing.p2p.<af>=False` override cannot reach them -- vary the pools instead
# and the "alt" render comes back byte-identical, which shape 2 then reports as "family not
# rendered" on a template that renders it perfectly well. Measured: that false positive fired on
# both arcos and dnos before this map existed.
SPLIT_LINKS = [("n2","ipv4"), ("n3","ipv6")]


def render(name, lo_af, link_af, tmp, split=False, split_links=None):
  """Render one case.

  Returns ({module: config}, loopback_ifname, {ifname: {families}}) or (None, error, {}) --
  three elements on BOTH paths. The failure path returned two for a while and every caller
  unpacked three, so `tools-af-sweep.py frr stp` died with a ValueError instead of printing the
  SKIPPED line it was designed to print, and shape 2's `if cfgs2 is None: continue` guard took
  the whole run down with it rather than skipping one comparison.
  """
  os.chdir(tmp)
  with open("t.yml","w") as f:
    f.write(f"provider: external\ndefaults.device: {DEVICE}\n")
    f.write("module: [ " + ", ".join(MODULES) + " ]\n")
    if "bgp" in MODULES:
      f.write("bgp.as: 65000\n")
    # "the device owns its loopback" is the physical case, and it is what makes a stranded
    # per-AF construct reachable: with the loopback gone, a family whose only member is a link
    # of the other family has nothing at all. split-phys is that shape AND per-interface
    # divergence at once, which is what a real hardware adjacency looks like.
    if "phys" in name:
      f.write("groups:\n  all:\n    vars:\n      netlab_manage_identity: False\n")
    if split:
      # Per-LINK families, which the global addressing overrides below cannot express: n1 ends
      # up with one v4-only interface and one v6-only interface, in the same area, same module.
      # Per-INTERFACE, not `prefix.<af>: False`: a prefix dict carrying only one family
      # REPLACES the pool allocation, so the link loses the other family too and the node is
      # back to uniform -- measured, swp1 came back with neither address.
      pairs = split_links or SPLIT_LINKS
      f.write("nodes: [ n1, " + ", ".join(p for p, _ in pairs) + " ]\n")
      f.write("links:\n")
      for peer, af in pairs:
        off = "ipv6" if af == "ipv4" else "ipv4"
        f.write(f"- n1: {{ {off}: False }}\n  {peer}: {{ {off}: False }}\n")
    else:
      f.write("nodes: [ n1, n2 ]\nlinks: [ n1-n2 ]\n")
  ov = []
  for af in ("ipv4","ipv6"):
    if af not in link_af and not split: ov += ["-s", f"addressing.p2p.{af}=False"]
    if af not in lo_af:   ov += ["-s", f"addressing.loopback.{af}=False"]
  r = subprocess.run(["netlab","create","t.yml"]+ov, capture_output=True, text=True)
  if r.returncode:
    return None, (r.stdout+r.stderr).strip().splitlines()[:2], {}
  cfgs = {}
  for m in ["initial"] + MODULES:      # initial is rendered unconditionally, not a module: value
    p = f"node_files/n1/{m}"
    if os.path.exists(p):
      cfgs[m] = open(p).read()
  lb = subprocess.run(["netlab","inspect","nodes.n1.loopback.ifname"],
                      capture_output=True, text=True).stdout.strip() or None
  return cfgs, lb, interface_afs()


def interface_afs():
  """{ifname: {families}} for n1, read from the transformed topology rather than guessed."""
  raw = subprocess.run(["netlab","inspect","nodes.n1.interfaces"],
                       capture_output=True, text=True).stdout
  try:
    import yaml
    data = yaml.safe_load(raw) or []
  except Exception:                                  # noqa: BLE001 -- a map we cannot read is no map
    return {}
  out = {}
  for intf in data if isinstance(data, list) else []:
    if not isinstance(intf, dict) or "ifname" not in intf:
      continue
    out[str(intf["ifname"])] = {af for af in ("ipv4","ipv6") if intf.get(af)}
  return out


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


def one_case(name, lo_af, link_af, split=False):
  notes, limits = [], []
  tmp = tempfile.mkdtemp(prefix="afs.")
  try:
    cfgs, lb, if_afs = render(name, lo_af, link_af, tmp, split)
    base_links = list(SPLIT_LINKS)
    if cfgs is None:
      # NOT a template defect. The topology this harness generates is fixed (two nodes, one
      # link), so a module with requirements it cannot express -- stp needs vlan, vrf and evpn
      # need their own structures -- fails to transform, as does a device that declares a family
      # unsupported. Reporting those as findings makes the harness cry wolf: a red count that
      # says nothing about template correctness. They are surfaced separately and not counted.
      return [], [f"cannot express this case: {lb[0][:70]}"]

    # An empty interface map disables shape 4 completely, and silence would then mean both
    # "nothing wrong" and "not checked". Say which -- as a LIMIT, not a finding: counting it
    # would report a red run for a check that never executed, which is the same cry-wolf the
    # skipped-case machinery exists to avoid.
    limits = [] if if_afs else ["shape 4 not run: no interface map for n1"]
    for mod, cfg in cfgs.items():
      for blk in empty_blocks(cfg):
        notes.append(f"{mod}: EMPTY <{blk}>")
      for bad in af_mismatches(cfg, if_afs):
        notes.append(f"{mod}: MISMATCH {bad}")

    # Shape 3 -- a device that owns its loopback must not have it rendered. Keyed on the
    # loopback name netlab actually assigned, not a literal: DNOS is lo0 and ArcOS loopback0,
    # and a hardcoded "lo0" is silently blind on every device that names it otherwise.
    if "phys" in name and lb:
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
      # For the split case the families live per link, so vary THOSE: every link carrying the
      # family under test switches to the other one, which is a real change to what n1 has.
      alt_links = None
      if split:
        other = "ipv6" if af == "ipv4" else "ipv4"
        alt_links = [(p, other if a == af else a) for p, a in base_links]
        if alt_links == base_links:
          continue
      tmp2 = tempfile.mkdtemp(prefix="afs2.")
      try:
        cfgs2, _, _ = render(name, alt_lo, alt, tmp2, split, alt_links)
        if cfgs2 is None:
          continue
        for mod in MODULES:
          if cfgs.get(mod,"").strip() and cfgs.get(mod,"") == cfgs2.get(mod,""):
            notes.append(f"{mod}: {verb} {af} changes nothing -- family not rendered")
      finally:
        os.chdir("/"); shutil.rmtree(tmp2, ignore_errors=True)
  finally:
    os.chdir("/"); shutil.rmtree(tmp, ignore_errors=True)
  return notes, limits


def main():
  total = n_skip = 0
  for name, lo_af, link_af, split in CASES:
    found, skipped = one_case(name, lo_af, link_af, split)
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
    # Two different limits reach this counter -- a case the harness could not render at all, and
    # a case that rendered but whose interface map was unreadable so shape 4 did not run. The
    # per-case line says which; a summary that names only the first misdescribes the second.
    tail += f"  (skipped={n_skip}, see the per-case reason above)"
  print(f"\n  device={DEVICE} modules={','.join(MODULES)}{tail}")
  return 1 if total else 0


if __name__ == "__main__":
  sys.exit(main())
