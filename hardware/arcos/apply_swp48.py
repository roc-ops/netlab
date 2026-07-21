#!/usr/bin/env python3
"""One-shot HUMAN-APPROVED apply of the swp48 candidate to the live roc-leaf2 (issue #39).

Sequence (fail-closed, auto-rollback on any anomaly):
  1. pre-capture the full running-config baseline (read-only, in memory -- never to disk).
  2. render the candidate from the SAME plan + re-run scan_config right before applying.
  3. apply via confd load merge + commit (gated: allow=True AND ARCOS_HW_ALLOW_PUSH=YES).
  4. verify: (a) mgmt reachable (show version), (b) protected stanzas byte-unchanged
     (baseline_diff), (c) swp48 shows 198.19.100.1/24, (d) no unexpected drift elsewhere.
  5. on ANY anomaly -> rollback selective 0 + re-verify, and exit non-zero.

Configs are held in memory; only redacted copies are ever printed. The AAA $6$ hash and the
password never touch disk.
"""
from __future__ import annotations
import sys, re, difflib
sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
import arcos_hw_bridge as B
import arcos_guardrail as G
import arcos_transport as T
import yaml

PLAN, TPL, NODE = sys.argv[1], sys.argv[2], (sys.argv[3] if len(sys.argv) > 3 else "roc-leaf2")
REDACT = lambda s: re.sub(r"(admin-password )\$6\$\S+", r"\1$6$__REDACTED__", s)


def iface_stanza(cfg: str, ifn: str) -> list[str]:
    lines = cfg.splitlines()
    for i, ln in enumerate(lines):
        if re.match(rf"^interface\s+{re.escape(ifn)}\s*$", ln):
            body = [ln.rstrip()]; j = i + 1
            while j < len(lines) and (lines[j].startswith(" ") or lines[j].startswith("\t")):
                body.append(lines[j].rstrip()); j += 1
            return body
    return []


def full_diff(before: str, after: str) -> list[str]:
    return [d for d in difflib.unified_diff(before.splitlines(), after.splitlines(),
            "live-before", "live-after", lineterm="")]


def main():
    t = T.Transport()
    plan = yaml.safe_load(open(PLAN))

    print("## 1. PRE-CAPTURE baseline (read-only)")
    pre = t.show_running_config()
    protected = G.discover_protected(pre)
    print(f"   baseline {len(pre.splitlines())} lines; protected reachability set: "
          f"{sorted(protected - G.MGMT_IFNAMES)} (+ mgmt lifeline)")

    print("\n## 2. RENDER candidate + RE-SCAN guardrail (fail-closed, right before apply)")
    candidate = B.render_node(plan, TPL, NODE)
    try:
        G.scan_config(candidate, protected)
    except G.GuardrailViolation as e:
        print(f"   [ABORT] guardrail refused: {e}"); sys.exit(1)
    print("   [PASS] scan_config -- candidate touches no protected region")
    print("   candidate:\n" + "\n".join("     " + l for l in candidate.rstrip().splitlines()))

    print("\n## 3. APPLY (confd load merge + commit)")
    out = t.apply_push(candidate, allow=True)
    print("   confd: " + " | ".join(l.strip() for l in out.splitlines() if l.strip())[:300])
    if "Commit complete" not in out and "No modifications" not in out:
        print("   [WARN] unexpected commit output -- proceeding to verify, will rollback if bad")

    print("\n## 4. VERIFY")
    ok = True
    # (a) mgmt reachable
    try:
        ver = t.show_version()
        reach = "Software Version: S8.5.1A" in ver
    except Exception as e:
        reach = False; ver = str(e)
    print(f"   (a) mgmt reachable + show version: {'OK' if reach else 'FAILED'}")
    ok &= reach

    post = t.show_running_config()
    # (b) protected stanzas byte-unchanged
    drift = G.baseline_diff(pre, post, protected)
    print(f"   (b) protected stanzas byte-unchanged: {'OK (no drift)' if not drift else 'DRIFT!'}")
    if drift:
        ok = False
        for k, d in drift.items():
            print(f"       ## {k}\n" + "\n".join("       " + REDACT(x) for x in d))
    # (c) swp48 has the ip
    sw = iface_stanza(post, "swp48")
    has_ip = any("198.19.100.1" in l for l in sw)
    print(f"   (c) swp48 shows 198.19.100.1/24: {'OK' if has_ip else 'MISSING'}")
    print("\n".join("       " + l for l in sw))
    ok &= has_ip
    # (d) no unexpected drift: full before/after diff must be swp48-only
    fd = full_diff(pre, post)
    added = [l for l in fd if l.startswith("+") and not l.startswith("+++")]
    removed = [l for l in fd if l.startswith("-") and not l.startswith("---")]
    unexpected = [l for l in (added + removed)
                  if not re.search(r"198\.19\.100\.1|prefix-length 24|subinterface 0|exit", l)]
    print(f"   (d) full-config diff is swp48-only: "
          f"{'OK' if not unexpected else 'UNEXPECTED DRIFT!'}")
    print("       full before->after diff:")
    print("\n".join("         " + REDACT(l) for l in fd))
    ok &= not unexpected

    if ok:
        print("\n## RESULT: SUCCESS -- all of (a)-(d) pass. Change applied cleanly.")
        sys.exit(0)

    print("\n## ANOMALY DETECTED -> ROLLBACK (rollback selective 0)")
    print("   preview:\n" + "\n".join("     " + l for l in
                                       REDACT(t.rollback_preview(0)).splitlines()))
    rb = t.rollback_selective(0, allow=True)
    print("   rollback confd: " + " | ".join(l.strip() for l in rb.splitlines() if l.strip()))
    post2 = t.show_running_config()
    back = G.baseline_diff(pre, post2, protected) == {} and not [
        l for l in full_diff(pre, post2) if l.startswith(("+", "-"))
        and not l.startswith(("+++", "---"))]
    print(f"   post-rollback == pre-baseline: {'OK, restored' if back else 'STILL DIRTY!'}")
    sys.exit(2)


if __name__ == "__main__":
    main()
