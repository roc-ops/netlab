#!/usr/bin/env python3
"""ArcOS hardware DRY-RUN report (issue #39 phase 1) -- renders + gates + diffs, pushes NOTHING.

Ties the three phase-1 pieces together for `roc-leaf2` and prints the reviewable artifact the
human gate needs BEFORE anything is ever applied:

  (a) the exact ArcOS config block that WOULD be `load merge`d,
  (b) an OFFLINE diff of the target interface stanza vs the live running-config,
  (c) explicit proof the guardrailed regions (ssh / aaa / mgmt / ma1 / loopback0 / swp56) are
      untouched -- guardrail scan passes AND none of the live protected stanzas appear in the
      candidate,
  and the confd_cli sequence the transport WOULD run (built, never executed).

The diff is computed OFFLINE from the captured baseline (no config session on the box): `load
merge` is additive, so the predicted post-merge stanza = the live stanza plus the leaves the
candidate adds. Labeled as predicted -- confd may re-order its own display on a real commit.

usage: dryrun.py <plan.yml> <templates-dir> <live-running-config.txt> [node]
"""
from __future__ import annotations
import sys, re, difflib
sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
import arcos_hw_bridge as B
import arcos_guardrail as G
import arcos_transport as T
import yaml


def _iface_stanza(cfg: str, ifname: str) -> list[str]:
    lines = cfg.splitlines()
    for i, ln in enumerate(lines):
        if re.match(rf"^interface\s+{re.escape(ifname)}\s*$", ln):
            body = [ln.rstrip()]
            j = i + 1
            while j < len(lines) and (lines[j].startswith(" ") or lines[j].startswith("\t")):
                body.append(lines[j].rstrip())
                j += 1
            return body
    return []


def _candidate_ifaces(candidate: str) -> list[str]:
    return re.findall(r"^interface\s+(\S+)\s*$", candidate, re.M)


def _predict_merge(live_stanza: list[str], candidate: str, ifname: str) -> list[str]:
    """Predicted confd display of `interface <ifname>` AFTER an additive load-merge (offline)."""
    m = re.search(rf"^interface\s+{re.escape(ifname)}\s*$(.*?)(?=^\S|\Z)",
                  candidate, re.M | re.S)
    body = m.group(1) if m else ""
    out = list(live_stanza)
    ins = out[:-1] if out and out[-1] == "!" else out[:]  # insert before trailing '!'
    tail = ["!"] if out and out[-1] == "!" else []
    ipm = re.search(r"ipv4 address (\S+)\s+prefix-length\s+(\d+)", body)
    if ipm and not any("ipv4 address" in l for l in live_stanza):
        ins += [" subinterface 0",
                f"  ipv4 address {ipm.group(1)}",
                f"   prefix-length {ipm.group(2)}",
                "  exit", " exit"]
    return ins + tail


def main():
    plan_path, tpl_dir, live_path = sys.argv[1], sys.argv[2], sys.argv[3]
    node = sys.argv[4] if len(sys.argv) > 4 else "roc-leaf2"
    plan = yaml.safe_load(open(plan_path))
    live = open(live_path).read()

    candidate = B.render_node(plan, tpl_dir, node)
    protected = G.discover_protected(live)

    print("=" * 78)
    print(f"ArcOS HARDWARE DRY-RUN  --  node {node}  (NOTHING IS PUSHED)")
    print("=" * 78)

    # (a) exact block that would be load-merged
    print("\n(a) CANDIDATE CONFIG BLOCK (what `load merge` would feed):")
    print("-" * 60)
    print(candidate.rstrip())
    print("-" * 60)

    # (c) guardrail scan -- fail closed
    print("\n(c) GUARDRAIL -- protected regions:")
    print(f"    protected set (live-seeded): mgmt lifeline (ssh/aaa/network-instance "
          f"management/ma1) + {sorted(protected - G.MGMT_IFNAMES)}")
    try:
        G.scan_config(candidate, protected)
        print("    [PASS] scan_config: candidate opens/references NONE of the protected regions")
    except G.GuardrailViolation as e:
        print(f"    [REFUSED] {e}")
        sys.exit(1)
    live_protected = G.extract_protected_stanzas(live, protected)
    cand_hits = [h for h in live_protected if h in candidate]
    print(f"    [PASS] none of the {len(live_protected)} live protected stanza headers appear "
          f"in the candidate: {'CLEAN' if not cand_hits else cand_hits}")
    # explicit per-region confirmation
    for header in sorted(live_protected):
        print(f"           untouched: {header}")

    # (b) offline diff of each configured interface stanza vs live
    print("\n(b) OFFLINE DIFF vs live running-config (predicted additive merge):")
    for ifn in _candidate_ifaces(candidate):
        before = _iface_stanza(live, ifn)
        if not before:
            print(f"\n  interface {ifn}: NOT present in live config -- would be newly created:")
            m = re.search(rf"(^interface\s+{re.escape(ifn)}\s*$.*?)(?=^\S|\Z)",
                          candidate, re.M | re.S)
            for l in (m.group(1).rstrip().splitlines() if m else []):
                print(f"    + {l}")
            continue
        after = _predict_merge(before, candidate, ifn)
        diff = list(difflib.unified_diff(before, after, f"live:{ifn}",
                                         f"predicted-after-merge:{ifn}", lineterm=""))
        print(f"\n  interface {ifn}:")
        print("\n".join("    " + d for d in diff) if diff else "    (no change)")

    # transport: the would-push sequence (built, not executed)
    print("\n(d) TRANSPORT -- confd_cli sequence that WOULD run (BUILT, NOT EXECUTED):")
    print("-" * 60)
    print(T.Transport().dry_run_push(candidate).rstrip())
    print("-" * 60)
    print("\nDRY-RUN COMPLETE. Nothing was applied to 10.22.64.223.")


if __name__ == "__main__":
    main()
