#!/usr/bin/env python3
"""Address-family sweep with mixed-AF coverage and an empty-construct check.

The original sweep varied the address family uniformly across a topology, so it only ever
produced nodes where every interface and the loopback shared one AF profile. That misses the
shape physical hardware actually produces -- a node whose interfaces disagree with each other,
or with the loopback -- and it let a stranded "instance ospfv3" through on DNOS (issue #73).

Three shapes are checked:

  1. a construct emitted for a family the node does not have at all
  2. a family dropped when the node has both
  3. a per-AF construct emitted with NO members  <-- the one that was missing

Usage: af_sweep.py <device> [module ...]
"""
import os, re, shutil, subprocess, sys, tempfile

DEVICE  = sys.argv[1] if len(sys.argv) > 1 else "dnos"
MODULES = sys.argv[2:] or ["ospf"]

# (name, loopback AFs, link AFs) -- the last three are the mixed cases the old sweep never made
CASES = [
  ("v4only",        ["ipv4"],         ["ipv4"]),
  ("dualstack",     ["ipv4","ipv6"],  ["ipv4","ipv6"]),
  ("v6only",        ["ipv6"],         ["ipv6"]),
  ("lo-v6/link-v4", ["ipv6"],         ["ipv4"]),
  ("lo-v4/link-v6", ["ipv4"],         ["ipv6"]),
  # "physical" = the device owns its loopback, so netlab excludes it (issue #70). This is the
  # shape that produced the stranded ospfv3 instance, and the old sweep could not express it.
  ("phys/link-v4",  ["ipv4","ipv6"],  ["ipv4"]),
  ("phys/link-v6",  ["ipv4","ipv6"],  ["ipv6"]),
]

# A block whose body holds only these keys has no members -- it is a stranded construct.
# An area / address-family header counts as metadata as far as the PARENT construct is
# concerned: an instance holding only an empty area is still a stranded instance.
METADATA = re.compile(r"^\s*(router-id|administrative-distance|log-adjacency|global |area |!|$)")
# Only constructs where EMPTY IS WRONG. An OSPF instance or area with no interfaces is a
# stranded stanza. A BGP "address-family" with an empty body is NOT -- that is simply how
# DNOS enables an address family, and the production box carries several of them. Including
# address-family here produced 20 false positives, which would have led to "fixing" config
# that was already correct.
OPENER   = re.compile(r"^(\s*)(instance \S+|area \S+)\s*$")


def render(case, lo_af, link_af, tmp):
  os.chdir(tmp)
  with open("t.yml","w") as f:
    f.write(f"provider: external\ndefaults.device: {DEVICE}\n")
    f.write("module: [ " + ", ".join(MODULES) + " ]\n")
    if "bgp" in MODULES:
      f.write("bgp.as: 65000\n")
    if case.startswith("phys"):
      f.write("groups:\n  all:\n    vars:\n      netlab_manage_identity: False\n")
    f.write("nodes: [ n1, n2 ]\nlinks: [ n1-n2 ]\n")
  ov = []
  for af in ("ipv4","ipv6"):
    ov += [] if af in link_af else ["-s", f"addressing.p2p.{af}=False"]
    ov += [] if af in lo_af   else ["-s", f"addressing.loopback.{af}=False"]
  r = subprocess.run(["netlab","create","t.yml"]+ov, capture_output=True, text=True)
  if r.returncode:
    return None, (r.stdout+r.stderr).strip().splitlines()[:3]
  out = {}
  for m in ["initial"] + MODULES:          # initial is always rendered; it is not a module: value
    p = f"node_files/n1/{m}"
    if os.path.exists(p):
      out[m] = open(p).read()
  return out, None


def empty_blocks(cfg):
  """Shape 3: an indented block that opens a per-AF construct but holds no members."""
  lines, bad = cfg.splitlines(), []
  for i, ln in enumerate(lines):
    m = OPENER.match(ln)
    if not m:
      continue
    indent = len(m.group(1))
    body = []
    for nxt in lines[i+1:]:
      if nxt.strip() and (len(nxt) - len(nxt.lstrip())) <= indent:
        break
      body.append(nxt)
    # An empty body is the emptiest case of all -- do not skip it. Skipping it is exactly
    # why the first version of this sweep passed clean either side of a real defect.
    if all(METADATA.match(b) for b in body):
      bad.append(m.group(2).strip())
  return bad


def main():
  fails = 0
  for name, lo_af, link_af in CASES:
    tmp = tempfile.mkdtemp(prefix="afs.")
    try:
      cfgs, err = render(name, lo_af, link_af, tmp)
      if err:
        print(f"  {name:16} RENDER FAILED: {err[0][:70]}")
        fails += 1
        continue
      notes = []

      # SHAPE 2 -- absence. The previous version only looked for constructs that should not be
      # there, so a template producing NOTHING passed clean. A module that was asked for must
      # render something for every family the node actually has.
      for mod in MODULES:
        cfg = cfgs.get(mod, "")
        if not cfg.strip():
          notes.append(f"{mod}: rendered NOTHING")
          fails += 1
          continue
        for af in ("ipv4","ipv6"):
          if af in link_af and not any(t in cfg for t in (f"{af}-address", f"{af}-unicast",
                                                          "ospfv3" if af == "ipv6" else "instance ospf")):
            notes.append(f"{mod}: no {af} construct although the node has {af}")
            fails += 1

      # PHYSICAL -- the device owns its loopback, so nothing may render it. The old phys cases
      # gave the loopback both families, which made the spurious-AF check structurally unable to
      # fire on it: the cases named for the physical regression could not detect it.
      if name.startswith("phys"):
        for mod, cfg in cfgs.items():
          if "lo0" in cfg:
            notes.append(f"{mod}: renders lo0 although the device owns its loopback")
            fails += 1

      for mod, cfg in cfgs.items():
        for blk in empty_blocks(cfg):
          notes.append(f"{mod}: EMPTY <{blk}>")
          fails += 1
        for af, other in (("ipv4","ipv6"), ("ipv6","ipv4")):
          if af not in lo_af and af not in link_af:
            for pat in (f"{af}-address", f"{af}-unicast"):
              if pat in cfg:
                notes.append(f"{mod}: {pat} present but node has no {af}")
                fails += 1
      print(f"  {name:16} {'OK' if not notes else '; '.join(notes)}")
    finally:
      os.chdir("/")
      shutil.rmtree(tmp, ignore_errors=True)
  print(f"\n  device={DEVICE} modules={','.join(MODULES)}  findings={fails}")
  return 1 if fails else 0


if __name__ == "__main__":
  sys.exit(main())
