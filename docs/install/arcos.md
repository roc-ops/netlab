# Arrcus ArcOS

ArcOS runs as a **native containerlab container** (`arrcus_arcos` kind). *netlab* builds and
configures it over containerlab, and — uniquely among the VM-based open NOS devices — validates it
with the **stock `netlab ... --validate` framework**, with **no netlab-core changes**.

For the device-level caveats (management bootstrap, the disabled SSH/NETCONF/gNMI, the FRR-MTU
interop note, and the per-image feature gaps) see [](../caveats.md#caveats-arcos). This page
documents how ArcOS reaches full validation and what the virtual image cannot verify.

## Validation transport (docker-exec → confd_cli)

On the `arcos:8.2.1A.P2` container image the baked-in startup config fails to load, so SSH, NETCONF
and gNMI all boot **disabled**. *netlab* therefore deploys **and** validates over the same path:
Ansible's **docker** connection running `confd_cli` inside the container.

The stock validation framework reads DUT state by running the device's `netlab_show_command` over
the node connection (for a clab/docker node that is `docker exec`). ArcOS defines it in
`netsim/devices/arcos.yml` under `clab.group_vars`:

```yaml
netlab_show_command:
  [ bash, -c, 'f=$(mktemp); for i in $(seq 1 60); do
     echo "show $@ | display json" | confd_cli -C -u admin > $f 2>/dev/null;
     python3 -c "import sys,json; json.load(open(sys.argv[1]))" $f 2>/dev/null &&
     { cat $f; rm -f $f; exit 0; }; sleep 1; done; cat $f; rm -f $f' ]
```

The framework replaces `$@` with the path a check's `show_*` function returns, runs
`show <path> | display json` over `confd_cli`, and JSON-parses stdout into the `_result` global. Each
per-module plugin (`netsim/validate/<module>/arcos.py`) then walks the **OpenConfig JSON** for the
expected state. ArcOS speaks OpenConfig JSON (not FRR), so — unlike `sonic_clab`, which aliases the
`frr` validators — every ArcOS check is a real OpenConfig-JSON parser.

Two device quirks are absorbed inside `netlab_show_command`, so the framework itself needs no
changes:

- **Transient malformed JSON during convergence.** `confd | display json` on a large subtree can
  briefly emit invalid JSON while a protocol is churning. netlab treats a parse failure as
  unrecoverable and its `wait:` is a one-time up-front delay consumed by the multi-minute boot, so
  the command **retries confd itself** (≤60×, each attempt validated with `python3 json.load`) until
  it parses — which also lets adjacencies/sessions reach steady state.
- **The validate path double-wraps in `bash -c`.** `connect.py` runs the script arg as
  `bash -c "<script>"`, so in the committed device file every shell `$` is written `\$` to survive
  the outer shell.

## Validated modules

Each module has a plugin under `netsim/validate/<module>/arcos.py`, aggregated by
`netsim/validate/arcos.py`, and a self-contained ArcOS-to-ArcOS test under
`tests/integration/platform/arcos/`. All pass a clean fresh-boot
`netlab up -d arcos <test> --validate`.

| Module | Test | Checks (OpenConfig JSON) |
|---|---|---|
| OSPFv2 | `02-ospf.yml` | `ospf_neighbor` — neighbor with `adjacency-state` `NEIGHBOR_FULL` |
| BGP | `03-bgp.yml` | `bgp_neighbor` — peer `session-state` `ESTABLISHED`; `bgp_prefix` — received prefix in the BGP RIB |
| IS-IS | `04-isis.yml` | `isis_neighbor` — `adjacency-state` `UP`; `isis_prefix` — peer loopback in IS-IS reachability |
| routing (static) | `05-routing.yml` | `route_static` — committed static route (prefix + next-hop); see the note below |

### Note on the static-routing read-back

The ArcOS virtual container image does **not** populate an OpenConfig STATIC *operational-state* /
RIB tree (the state container is empty and no `afts`/`rib` operational path is exposed), so
`route_static` reads the committed route back over the **running-config** JSON — the same confd path
used to deploy it — and asserts a complete static-route entry (destination prefix + a configured
next-hop). The **dataplane install** was verified separately on the image (kernel
`ip route 10.0.0.2 via 10.1.0.2 dev swp1 proto static` present, and r1→r2 loopback ping 0% loss);
only the OpenConfig operational read-back is missing. This is a documented virtual-image gap, not a
functional one.

## Documented virtual-image gaps

ArcOS ships as a **full device with documented exceptions** — features that render and commit
cleanly but cannot be *verified* on the virtual container image because they are hardware- or
daemon-gated. These are tracked and must not be reported as validated:

- **VRRP / FHRP datapath (gateway module) — not verifiable (issue #22).** The `gateway.protocol:
  vrrp` config renders and commits, but the virtual container image cannot exercise VRRP
  master/backup election or the virtual-IP datapath (no VRRP keepalive/ARP datapath on the
  container), so no `--validate` check is shipped for it.
- **EVPN type-5 (symmetric IRB / L3VNI) routes do not originate — not verifiable (issue #34).**
  `vrfs.<name>.evpn.transit_vni` renders and commits, but the control plane never originates a
  route-type-5 prefix on this image, so only the L2VNI / distributed-anycast-gateway EVPN form is
  declared supported.
- **SR-MPLS / LDP — control-plane only.** Sessions, FEC/label bindings and prefix-SIDs come up, but
  the container image has no kernel MPLS platform-labels, so label forwarding cannot be tested.

See [](../caveats.md#caveats-arcos) for the full per-image feature-gap list (redistribution import
sources, routing-policy community scope, per-VRF instance-tag namespace, OSPFv2 area
authentication, VRF route-target leaking).

## Upstreaming note

ArcOS reaches stock `--validate` over docker-exec + confd_cli, the same way SONiC-clab does, so it
does **not** need the validation-transport (Ansible/gNMI) work that the VM-based OcNOS device
requires. The ArcOS validation coverage above is entirely per-device plugins + tests — **no
netlab-core changes**.
