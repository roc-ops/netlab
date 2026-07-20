# Installing IP Infusion OcNOS

netlab runs **IP Infusion OcNOS** as a [containerlab](clab.md)-provisioned device: a vrnetlab-packaged
OcNOS VM. Only the **clab** provider is supported (no Vagrant box).

## Container image

* Build the vrnetlab OcNOS container from the OcNOS `qcow2` using vrnetlab's `ipinfusion_ocnos` kind.
* Tag it `vrnetlab/ipinfusion_ocnos:<version>`. Verified against **7.0.0-262** (and 6.5.2-101).
* The device sets clab `kind: ipinfusion_ocnos`; point it at your image:

```
defaults.devices.ocnos.clab.image: vrnetlab/ipinfusion_ocnos:7.0.0-262
```

## Configuration deployment

OcNOS configuration is pushed with the **`ipinfusion.ocnos` Ansible collection** over `network_cli`
(the collection drives the interactive `cmlsh` shell). Install it once:

```
ansible-galaxy collection install ipinfusion.ocnos
```

netlab uses raw SSH only for the mgmt console, **not** for running show/exec commands (see the
validation note below). Device settings: `interface_name eth{ifindex}`, `mgmt_if eth0`, loopbacks
`lo`/`loopbackN`.

## Supported configuration modules

`initial`, `ospf` (+areas/NSSA), `bgp` (+plugins/policy/multihop), `isis`, `vrf` (+isis), `vlan`,
`lag` (+passive), `gateway`, `dhcp`/relay, `stp`, `mpls`, `sr` (SRGB), `vxlan`, `evpn` (MPLS), `bfd`,
`gre`. See `netsim/devices/ocnos.yml` `features:` for the authoritative list; support level is
**best-effort** (see `docs/caveats.md`).

## Validation over the Ansible transport (`netlab validate`)

OcNOS's `ocnos` user has a **restricted shell**: it drops into `cmlsh` only on an *interactive*
login. `ssh ocnos@node "show ..."` (and every `cmlsh -e/-c` variant) returns ``Try `cmlsh --help'`` --
there is **no non-interactive exec** -- and OcNOS emits CLI **text**, not JSON. netlab's stock
`netlab validate` fetches show data over `netlab connect` (SSH), which therefore cannot drive OcNOS.

OcNOS resolves this with a new **non-interactive connection transport** -- `ansible_connect`
in `netsim/cli/connect.py`, the Ansible peer of the existing `docker_connect` (docker-exec)
and `ssh_connect` transports. It is a generic netlab-core hook: any device whose CLI lacks a
non-interactive SSH exec can opt in via `devices/<dev>.yml` `group_vars`:

```
netlab_validate_transport: ansible
netlab_validate_ansible_module: ipinfusion.ocnos.ocnos_command
```

With this, non-interactive command/show execution (`netlab validate`, `netlab connect --show ...`)
runs through `ipinfusion.ocnos.ocnos_command` against the netlab-generated inventory, while an
*interactive* `netlab connect` still uses SSH -> `cmlsh`. Because OcNOS output is text, the OcNOS
validators (`netsim/validate/<module>/ocnos.py`, re-exported from `netsim/validate/ocnos.py`) use
the `exec_`/`valid_` contract and screen-scrape the returned text.

Run the standard integration suite against an OcNOS device under test:

```
export NETLAB_DEVICE=ocnos NETLAB_PROVIDER=clab
netlab up  tests/integration/ospf/ospfv2/01-network.yml
netlab validate
```

Verified live (vrnetlab `ipinfusion_ocnos:7.0.0-262`, FRR probes): the OSPF, BGP and IS-IS
integration tests pass with native `netlab validate`, and DUT-side neighbor/prefix checks pass
over the Ansible transport.

## Validated modules (native `netlab validate`)

Live-verified against vrnetlab `ipinfusion_ocnos:7.0.0-262` with FRR / cEOS / Linux probes,
using the Ansible validation transport described above. "Probe" = the check runs on the
adjacent probe (interop); "DUT" = the check runs on the OcNOS device over the Ansible transport.

| Module | Integration test | Result |
|---|---|---|
| ospf (v2) | `ospf/ospfv2/01-network` | PASS 4/4 (probe) + 3/3 (DUT: neighbor Full, route present) |
| bgp | `bgp/01-ebgp-session` | PASS 3/3 (probe) + 3/3 (DUT: sessions Established, prefix present) |
| isis | `isis/01-ipv4` | PASS 5/5 (probe) + 2/2 (DUT: adjacency L1, prefix present) — needed the `dynamic-hostname` fix |
| vlan | `vlan/01-vlan-bridge-single` | PASS 1/1 (host-to-host ping across the bridge) |
| lag | `lag/01-l3-lag` | PASS (LAG active on both EOS probes + IPv4 ping; one warning-level path-MTU check) |
| stp | `stp/01-stp-priority` | PASS 2/2 (link forwarding + root-bridge priority) — needed the bridge-priority fix |
| vrf | `vrf/11-multi-vrf-ospf` | Single-area VRF fully green (per-VRF adjacency, routes, ping, inter-VRF isolation). See exception below for the multi-area sub-case. |

Two config-completeness fixes came out of this pass: IS-IS `dynamic-hostname` (peers can map the
DUT system-id to a name) and the STP customer-bridge `priority` (was never rendered).

## Documented exceptions

A "full" device may ship with clearly-documented exceptions; these are recorded rather than faked.

* **Multi-area OSPF inside a VRF** (`vrf/11` blue sub-case). OcNOS is a strict (Cisco-type) ABR: it
  will not originate inter-area type-3 summaries when its backbone (area 0) is *inactive* — here the
  VRF's only area-0 interface is a stub loopback, so two non-backbone areas connected only through the
  DUT do not exchange routes. (OSPF-in-VRF also defaults to MPLS-VPN "superbackbone" mode;
  `capability vrf-lite` clears that but not the inactive-backbone rule.) FRR/EOS are lenient ABRs and
  summarize anyway. Single-area VRF OSPF is unaffected and passes.
* **gateway / VRRP** — config generation is verified (out-of-tree runner), but the `gateway/02-vrrp`
  integration topology cannot be brought up on this build: netlab's clab renderer misplaces `mtu:`
  inside a bridge link's `endpoints` list (generic core bug, tracked on `fix/clab-render-mtu`, not
  OcNOS-specific).
* **dhcp relay** — the 3-piece OcNOS relay config is verified out-of-tree; the `dhcp/11-ipv4-relay`
  integration test needs a libvirt-based `dnsmasq` server probe, unavailable on this clab-only host.
* **EVPN datapath** (#25/#26), **GRE tunnel line-protocol** (#24), **VRF-bound-interface ingress**
  (#27), **per-VRF OSPFv3 + IPv6 VRF route-leak** (#28) — tracked platform/image limitations, config
  generation present where applicable.
