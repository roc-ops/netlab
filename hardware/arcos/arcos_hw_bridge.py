#!/usr/bin/env python3
"""Plan -> render bridge for ArcOS HARDWARE (issue #39 hardware phase 1).

The exact Casa pattern (casa_netlab_bridge.py), applied to the real ArcOS box: netlab PLANS the
switch as an `unmanaged` node -- addressing, interfaces (real swpX names via ifname), per-module
data -- but never deploys it. This bridge renders the in-tree netlab `arcos` device templates
(netsim/ansible/templates/<module>/arcos.j2 -- the SAME templates the container device uses;
only the transport differs) against that plan node, producing ArcOS confd/CLI config. The output
then goes through the ArcOS guardrail (arcos_guardrail.py) before anything could ever be pushed.
netlab never touches the live device.

Rendering note (identical to Casa's): the netlab templates use ansible collection filters
(`ansible.netcommon.ipv4/ipv6/ipaddr`, `ansible.utils.ipaddr`) whose dotted names don't parse in
plain Jinja2. We rewrite them to plain netaddr-backed shims -- byte-identical output for our
addressing, no ansible runtime needed.
"""
from __future__ import annotations
import sys, os, yaml, jinja2


def _ipaddr(value, query=""):
    """Minimal ansible ipaddr/ipv4/ipv6 shim (netaddr-backed) for standalone rendering."""
    import netaddr
    if value in (True, False, None):
        return value
    try:
        net = netaddr.IPNetwork(str(value))
    except Exception:
        return value
    q = str(query)
    if q in ("", "address"):
        return str(net.ip)
    if q == "netmask":
        return str(net.netmask)
    if q in ("prefix", "prefix_length"):
        return str(net.prefixlen)
    if q in ("0", "network/prefix", "subnet"):
        return str(net.cidr)
    return str(net.ip)


# dotted ansible filter name -> plain name we register below (order: longest first).
_FILTER_REWRITES = [
    ("ansible.netcommon.ipaddr", "ipaddr"),
    ("ansible.netcommon.ipv4", "ipaddr"),
    ("ansible.netcommon.ipv6", "ipaddr"),
    ("ansible.utils.ipaddr", "ipaddr"),
]


def _env(template_dir: str) -> jinja2.Environment:
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(template_dir),
                             trim_blocks=True, lstrip_blocks=True,
                             undefined=jinja2.ChainableUndefined)
    env.filters["ipaddr"] = _ipaddr
    return env


def _rewrite_filters(src: str) -> str:
    for dotted, plain in _FILTER_REWRITES:
        src = src.replace(dotted, plain)
    return src


def _node_data(loaded: dict, node: str) -> dict:
    if isinstance(loaded, dict) and "nodes" in loaded and node in loaded["nodes"]:
        return loaded["nodes"][node]
    return loaded


def _context(ndata: dict, node: str) -> dict:
    """Node data mapped into the ansible var namespace netlab templates render in.

    netlab exposes the node name as `inventory_hostname`; the arcos templates iterate
    `interfaces` (see initial/arcos.j2's header note) and read `loopback` directly.
    """
    ctx = dict(ndata)
    ctx.setdefault("inventory_hostname", ndata.get("name", node))
    lo = ndata.get("loopback")
    ifaces = ndata.get("interfaces", [])
    ctx.setdefault("netlab_interfaces", ([lo] if lo else []) + ifaces)
    return ctx


def configured_ifnames(ndata: dict) -> list[str]:
    """Interfaces the render will touch (data-plane + loopback if owned) -- never mgmt."""
    names = [i["ifname"] for i in ndata.get("interfaces", []) if "ifname" in i]
    lo = ndata.get("loopback")
    if isinstance(lo, dict) and "ifname" in lo:
        names.append(lo["ifname"])
    return names


def render_node(plan: dict, template_dir: str, node: str,
                modules: list[str] | None = None) -> str:
    """Render initial + each of the node's modules through the arcos templates."""
    ndata = _node_data(plan, node)
    ctx = _context(ndata, node)
    env = _env(template_dir)
    mods = ["initial"] + (modules if modules is not None else ndata.get("module", []))
    out = []
    for m in dict.fromkeys(mods):
        rel = f"{m}/arcos.j2"
        path = os.path.join(template_dir, rel)
        if not os.path.exists(path):
            out.append(f"! ---- {m}: no {rel} (module template not built) ----")
            continue
        src = _rewrite_filters(open(path).read())
        text = env.from_string(src).render(**ctx).rstrip()
        if text:
            out.append(text)
    return "\n!\n".join(out) + "\n"


def main():
    args = sys.argv[1:]
    running_cfg = None
    if "--running-config" in args:
        i = args.index("--running-config")
        running_cfg = open(args[i + 1]).read()
        del args[i:i + 2]
    allow_protected = False
    if "--allow-protected" in args:
        allow_protected = True
        args.remove("--allow-protected")
    if len(args) < 2:
        print("usage: arcos_hw_bridge.py <plan.yml> <templates-dir> [node] "
              "[--running-config <live.txt>] [--allow-protected]", file=sys.stderr)
        sys.exit(2)
    plan = yaml.safe_load(open(args[0]))
    node = args[2] if len(args) > 2 else "roc-leaf2"
    config = render_node(plan, args[1], node)

    # Guardrail gate at render time (mirrors Casa's --protect): refuse to emit config that
    # touches a protected region. The protected set is seeded from the LIVE running-config.
    if running_cfg is not None and not allow_protected:
        import arcos_guardrail as G
        protected = G.discover_protected(running_cfg)
        try:
            G.scan_config(config, protected)
        except G.GuardrailViolation as e:
            print(str(e), file=sys.stderr)
            sys.exit(1)
    print(config)


if __name__ == "__main__":
    main()
