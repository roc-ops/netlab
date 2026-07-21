#!/usr/bin/env python3
"""ArcOS management + reachability guardrail (issue #39 hardware phase 1).

The ArcOS analog of the Casa `network_guardrail.py`, for the REAL Edgecore AS7326-56X
`roc-leaf2` (ArcOS S8.5.1A @ 10.22.64.223). It fences the confd/ArcOS config regions that,
if changed, would either cut our management lifeline or corrupt this box's live EVPN-underlay
identity. No config netlab renders is allowed to touch them.

Two protected classes (mirrors Casa: a hardcoded mgmt lifeline + a live-seeded reachability set):

  MANAGEMENT -- ALWAYS protected, hardcoded (these are the box's lifeline, never netlab's):
    * `system ssh-server ...`          -- enable + permit-root-login: our SSH/confd_cli transport
    * `system aaa authentication ...`  -- admin-user password hash + AAA users
    * `network-instance management`    -- the mgmt VRF that holds ma1
    * `interface ma1`                  -- the mgmt port (10.22.64.223)

  REACHABILITY -- SEEDED FROM LIVE DISCOVERY, not hardcoded (like Casa's `reachability_protect`),
  because what is L3-addressed on the box is ground truth, not an assumption. `discover_protected`
  reads the running-config and protects every interface that currently carries an L3 address --
  here loopback0 (EVPN router-id 198.19.255.4) and swp56 (the live underlay uplink 198.19.19.11).
  netlab must never re-own these.

Fail CLOSED, three gates (same shape as the Casa guardrail):
  1. `scan_config`     -- token-scan rendered ArcOS CLI; refuse if it opens OR references a
                          protected interface, or a `system ssh-server` / `system aaa` /
                          `network-instance management` stanza.
  2. `capture_baseline`-- read-only pull of the protected stanzas from the live running-config.
  3. `baseline_diff`   -- post-(dry-run/push) diff of those stanzas; any drift == breached.

confd/ArcOS config is block-indented: a top-level stanza header sits at column 0, its body is
indented, and the block ends at the next column-0 line (`!` separators are column 0 too).
"""
from __future__ import annotations
import re, sys, json, difflib
from pathlib import Path


class GuardrailViolation(Exception):
    """Raised the instant a rendered/pushed line would touch a protected region."""


# The mgmt port name is fixed on this platform; the reachability ifnames are discovered.
MGMT_IFNAMES = {"ma1"}

# column-0 stanza headers / leaves that are always off-limits (management lifeline).
_PROTECTED_STANZA_RE = [
    re.compile(r"^system\s+ssh-server\b"),
    re.compile(r"^system\s+aaa\b"),
    re.compile(r"^network-instance\s+management\b"),
]


# ---- seed the reachability-protected set from the live running-config ---------
def discover_protected(running_config: str) -> set[str]:
    """ifnames the box currently L3-addresses -> reachability-protected (+ the mgmt port).

    An interface stanza is `interface <name>` at column 0; we protect it if its body carries
    an `ipv4 address` or `ipv6 address` leaf. ma1 is added unconditionally (mgmt lifeline).
    """
    protected = set(MGMT_IFNAMES)
    lines = running_config.splitlines()
    i = 0
    while i < len(lines):
        m = re.match(r"^interface\s+(\S+)\s*$", lines[i])
        if m:
            name = m.group(1)
            j = i + 1
            addressed = False
            while j < len(lines) and (lines[j].startswith(" ") or lines[j].startswith("\t")):
                if re.search(r"\b(ipv4|ipv6)\s+address\b", lines[j]):
                    addressed = True
                j += 1
            if addressed:
                protected.add(name)
            i = j
            continue
        i += 1
    return protected


# ---- gate 1: scan the rendered ArcOS config ----------------------------------
def _iface_ident(line: str) -> str | None:
    """If `line` opens an interface stanza (column 0), return the ifname it configures."""
    m = re.match(r"^interface\s+(\S+)\s*$", line)
    return m.group(1) if m else None


def scan_config(config_text: str, protected: set[str]) -> None:
    """Refuse if the rendered config configures OR references any protected region.

    Fail closed. Three ways a line is refused:
      (a) it OPENS `interface <protected>` (column 0),
      (b) it opens/sets a hardcoded mgmt stanza (`system ssh-server`, `system aaa`,
          `network-instance management`),
      (c) it REFERENCES a protected ifname as a bare token anywhere (e.g. `lldp interface ma1`,
          `overlay source-interface loopback0`, an ISIS `interface swp56`) -- anything that
          ropes a protected interface into the pushed config.
    """
    for raw in config_text.splitlines():
        line = raw.rstrip()
        # (b) hardcoded management stanzas
        for rx in _PROTECTED_STANZA_RE:
            if rx.match(line):
                raise GuardrailViolation(
                    f"REFUSED: line opens a protected management stanza: {line!r}")
        # (a) opening a protected interface stanza
        opened = _iface_ident(line)
        if opened is not None and opened in protected:
            raise GuardrailViolation(
                f"REFUSED: config opens `interface {opened}` -- protected "
                f"({'mgmt' if opened in MGMT_IFNAMES else 'reachability-critical, live-addressed'})")
        # (c) bare protected-ifname reference anywhere on the line. ifnames here are
        # alnum tokens (ma1/loopback0/swp56); guard word boundaries so swp56 != swp560
        # and loopback0 != loopback01.
        for ifn in protected:
            if re.search(rf"(?<![\w/]){re.escape(ifn)}(?![\w/])", line):
                raise GuardrailViolation(
                    f"REFUSED: line references protected interface {ifn!r}: {line!r}")


# ---- gates 2 & 3: baseline capture + post-push diff --------------------------
def _protected_headers(protected: set[str]) -> list:
    """Column-0 header matchers for every protected stanza (interfaces + mgmt lifeline)."""
    heads = [re.compile(rf"^interface\s+{re.escape(ifn)}\s*$") for ifn in protected]
    heads += _PROTECTED_STANZA_RE
    return heads


def extract_protected_stanzas(config_text: str, protected: set[str]) -> dict[str, list[str]]:
    """Pull each protected stanza's block from a running-config dump.

    Keyed by the header line. Block = the column-0 header + its indented body, up to (but not
    including) the next column-0 line. A single-leaf protected line (e.g. `system ssh-server
    enable true`) is its own one-line block.
    """
    heads = _protected_headers(protected)
    lines = config_text.splitlines()
    out: dict[str, list[str]] = {}
    i = 0
    while i < len(lines):
        line = lines[i].rstrip()
        if any(h.match(line) for h in heads):
            body = [line]
            j = i + 1
            while j < len(lines) and (lines[j].startswith(" ") or lines[j].startswith("\t")):
                body.append(lines[j].rstrip())
                j += 1
            out.setdefault(line, []).extend(body)
            i = j
            continue
        i += 1
    return out


def _norm(body: list[str]) -> list[str]:
    return [ln.rstrip() for ln in body if ln.strip() and ln.strip() != "!"]


def baseline_diff(before_cfg: str, after_cfg: str, protected: set[str]) -> dict[str, list[str]]:
    """{header: unified-diff} for each protected stanza that CHANGED.
    Empty dict == every protected region is byte-for-byte intact."""
    before = extract_protected_stanzas(before_cfg, protected)
    after = extract_protected_stanzas(after_cfg, protected)
    drift: dict[str, list[str]] = {}
    for key in set(before) | set(after):
        b, a = _norm(before.get(key, [])), _norm(after.get(key, []))
        if b != a:
            drift[key] = list(difflib.unified_diff(b, a, f"baseline:{key}",
                                                   f"after:{key}", lineterm=""))
    return drift


def capture_baseline(host: str | None = None) -> tuple[str, set[str]]:
    """Read-only: pull the live running-config and derive the protected set from it.
    Returns (running_config_text, protected_ifnames). Uses arcos_transport (show only)."""
    import arcos_transport as T
    cfg = T.Transport(host).show_running_config()
    return cfg, discover_protected(cfg)


# ---- CLI ---------------------------------------------------------------------
def _usage():
    print("usage:\n"
          "  arcos_guardrail.py --show <running-config.txt>            # print protected set\n"
          "  arcos_guardrail.py --scan <running-config.txt> <rendered-config.txt>\n"
          "  arcos_guardrail.py --stanzas <running-config.txt>        # dump protected stanzas\n"
          "  arcos_guardrail.py --diff <before.txt> <after.txt>",
          file=sys.stderr)


def main():
    a = sys.argv[1:]
    if not a:
        _usage(); sys.exit(2)
    mode = a[0]
    if mode == "--show":
        prot = discover_protected(Path(a[1]).read_text(errors="replace"))
        print("protected regions:")
        print("  management (hardcoded): system ssh-server, system aaa, "
              "network-instance management, interface ma1")
        print("  reachability (live-seeded, L3-addressed interfaces):")
        for ifn in sorted(prot - MGMT_IFNAMES):
            print(f"    interface {ifn}")
    elif mode == "--scan":
        prot = discover_protected(Path(a[1]).read_text(errors="replace"))
        cfg = Path(a[2]).read_text(errors="replace")
        try:
            scan_config(cfg, prot)
            print(f"OK: rendered config touches NONE of the {len(prot)} protected "
                  f"interfaces or the mgmt/aaa/ssh stanzas")
        except GuardrailViolation as e:
            print(str(e)); sys.exit(1)
    elif mode == "--stanzas":
        run = Path(a[1]).read_text(errors="replace")
        prot = discover_protected(run)
        st = extract_protected_stanzas(run, prot)
        print(json.dumps(st, indent=2))
    elif mode == "--diff":
        before = Path(a[1]).read_text(errors="replace")
        after = Path(a[2]).read_text(errors="replace")
        prot = discover_protected(before)
        drift = baseline_diff(before, after, prot)
        if not drift:
            print("OK: every protected region is byte-for-byte intact")
        else:
            print("DRIFT -- protected regions changed:")
            for key, d in drift.items():
                print(f"\n## {key}")
                print("\n".join(d))
            sys.exit(1)
    else:
        _usage(); sys.exit(2)


if __name__ == "__main__":
    main()
