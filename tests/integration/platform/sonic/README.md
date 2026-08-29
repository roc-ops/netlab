# SONiC (clab) smoke tests -- issue #40

Device-specific bring-up smoke tests for the `sonic` device under provider `clab`
(docker-sonic-vs on containerlab). Adapted from our out-of-tree reference implementation
(roccontlab netlab/sonic/) with `multilab.id` -> 52.

**Status of the "checked live" column below.** Read it as three separate claims -- a topology
that CREATES, a topology that DEPLOYS, and a topology whose datapath was actually checked --
because they have come apart here before.

Six rows were **re-verified live on 2026-08-29** against the converged `sonic` device
(containerlab 0.78.2, docker-sonic-vs:latest): `08-vlan`, `09-lag`, `10-vrrp-version`,
`11-vxlan-evpn`, `12-evpn-irb-l3vni`, `27-tunnel-gre`. Those rows say so and carry what was
measured. **Three of those six were wrong or broken when re-run**: `12-evpn-irb-l3vni` failed to
deploy (and fails on the retired device too), `27-tunnel-gre` could not even be created and then
failed to deploy for a second, independent reason, and `10-vrrp-version` recorded a VRRP state
that is not reached on either device.

Every other row was run live at some point, but against the RETIRED `sonic_clab` device on the
initial script as it stood then. The converged device renders a materially different initial
script (kernel MTU instead of a CONFIG_DB write, a config_db readiness retry, an FRR-daemon
readiness gate, a different restart placement, IPv6 guards), so those results carry over in
expectation, not in evidence. Given that half the rows actually checked turned out to be wrong,
treat an unmarked row as UNTESTED rather than as passing.

Re-running the whole suite and recording create / deploy / pass separately is tracked as
issue #87.

**These are container tests, despite the directory name.** The directory is named for the
DEVICE (`sonic`), because that is what netlab's integration-test runner keys on, but every
topology in it pins `provider: clab` and exercises the monolithic `docker-sonic-vs` image.
None of them says anything about the libvirt SONiC VM, which shares the device but is a
different architecture (multi-container, FRR reached through `docker exec bgp vtysh`) and
is untested. Read a green result here as the container works, never as the device works.

These tests were written against a separate `sonic_clab` device, which was retired in favour
of upstream's single-device shape (issue #86): the container now lives in the `clab:` block
of `netsim/devices/sonic.yml` and its templates are named `<module>/sonic-clab.j2`. The
topologies are unchanged apart from `device: sonic_clab` -> `device: sonic`.

| topology | module(s) exercised | what was checked live |
|---|---|---|
| `01-initial.yml` | initial | 1-node bring-up: hostname, loopback IP, interfaces |
| `02-ospf.yml` | ospf | adjacency Full (pure `ansible_network_os: frr` fallback, no SONiC-specific template) |
| `03-bgp.yml` | bgp | eBGP session Established (`bgp/sonic-clab.j2` override) |
| `04-isis.yml` | isis | adjacency Up (frr fallback) |
| `05-vrf.yml` | vrf, ospf | per-VRF OSPF adjacency Full (frr fallback) |
| `06-mpls-sr-l3vpn.yml` | isis, bgp, mpls, sr, vrf | LDP Operational, real kernel MPLS label FIB, VPNv4 Established, L3VPN ping 0% loss. **Needs a one-time host prereq: `sudo modprobe mpls_router mpls_iptunnel`** |
| `07-srv6.yml` | isis, srv6 | real kernel `seg6local` End/End.X routes, cross-node ISIS locator advertisement |
| `08-vlan.yml` | vlan | **re-verified live 2026-08-29** (s1<->s2 over the IRB SVI, IPv4 4/4 and IPv6 3/3, 0% loss).  IRB SVI + kernel bridge datapath, 0% loss (needed a `sonic-clab.j2` override + kernel-sync -- see below) |
| `09-lag.yml` | lag, ospf | **re-verified live 2026-08-29** (LACP runner, both members Selected, OSPF Full over PortChannel1).  LACP aggregate (teamd), OSPF Full over the PortChannel (needed a `sonic-clab.j2` override) |
| `10-vrrp-version.yml` | gateway, vrrp.version plugin | VRRP pinned to v2 -- **re-verified live 2026-08-29 on the converged device**: `show vrrp` reports `Protocol Version 2`. The previous **"Master state confirmed" claim is WRONG** and has been removed: VRRP sits in `Initialize` on both nodes, and identically so on the retired `sonic_clab` device, so it is a stale claim rather than a regression. Not investigated further. (Template lives in `netsim/extra/vrrp/version/sonic-clab.j2`, NOT `netsim/ansible/templates/` -- it's a node_config plugin, different search path.) |
| `11-vxlan-evpn.yml` | vlan, ospf, bgp, vxlan, evpn | **re-verified live 2026-08-29** (EVPN Established, 1 remote VTEP, h1<->h2 across the tunnel 5/5, 0% loss).  L2VNI up, remote VTEP learned, h1<->h2 ping across the tunnel 0% loss (needed a `sonic-clab.j2` override) |
| `12-evpn-irb-l3vni.yml` | vlan, vrf, ospf, bgp, vxlan, evpn | symmetric-IRB L3VNI inter-subnet ping 0% loss -- **re-verified live 2026-08-29** (L2VNI 10010 + L3VNI 10099 in `tenant`, h1 172.18.0.3 to h2 172.18.1.4 across subnets, 5/5). This **FAILED on the retired `sonic_clab` device** and fails there still: the IPv6 branch of the initial template lacked the VNI-backed-SVI guard, so it died on `Vlan1001 does not exist`. Fixed in this tree; the earlier pass recorded here could not have been against the topology in its current form. |
| `13-vrf-leak.yml` | vrf, bgp | VRF red's prefix leaks into VRF blue's RIB via BGP RT import |
| `14-bgp-session.yml` | bgp, bgp.session plugin | MD5 password applied, session Established (frr fallback) |
| `15-bgp-policy.yml` | bgp, bgp.policy plugin, routing | route-map locpref/weight/community all applied (frr fallback) |
| `16-ebgp-multihop.yml` | bgp, ebgp.multihop plugin, ospf, routing | loopback-to-loopback multihop session Established (frr fallback) |
| `17-ospf-areas.yml` | ospf, ospf.areas plugin | stub area adjacency Full (frr fallback) |
| `18-bgp-originate.yml` | bgp | originated prefix received by the eBGP peer (frr fallback) |
| `19-bfd.yml` | ospf, bfd | BFD session Up under OSPF (frr fallback; needed `features.ospf.bfd`/`features.bgp.bfd` device flags) |
| `20-ripv2.yml` | ripv2 | route learned via RIP (frr fallback) |
| `21-evpn-multihoming.yml` | lag, vlan, ospf, bgp, vxlan, evpn, evpn.multihoming plugin | Ethernet Segment on PortChannel1, dual-homed h1<->h2 ping 0% loss (needs the `h1-fixaddr/` custom-config addon, a generic `linux`-device host-addressing workaround, not SONiC-specific) |
| `22-ospfv3.yml` | ospf | OSPFv3 dual-stack: both AFs adjacency Full, ping6 0% loss (frr fallback) |
| `23-isis-v6-bgp-v6.yml` | isis, bgp | IS-IS adjacency Up (multi-topology IPv6), iBGP IPv6 AF Established (frr fallback) |
| `24-routing-v6.yml` | routing | IPv6 static route + IPv6 prefix-list both installed (frr fallback) |
| `25-vrf-v6-leak.yml` | vrf, bgp | VRF red's IPv6 prefix leaks into VRF blue's v6 RIB via BGP RT import (frr fallback) |
| `26-vrf-v6-ospfv3.yml` | vrf, ospf | per-VRF OSPFv3 adjacency Full (frr fallback) |
| `27-tunnel-gre.yml` | ospf, tunnel.gre plugin | kernel `ip_gre` tunnel, ping across the tunnel IPs 0% loss -- **re-verified live 2026-08-29** (IPv4 5/5, IPv6 4/4 across the tunnel). It was **BROKEN when re-run**, in two independent ways that masked each other: `tunnel.gre` was declared as a bool where the plugin tests list membership, so the topology could not even be created; and once that was fixed, the IPv6 branch of the initial template lacked the tunnel guard, so the deploy died on `tun0 is not valid`. Both fixed in this tree. An IPv6 UNDERLAY was also verified for the first time (real `ip6gre` netdev, IPv4 4/4 and IPv6 4/4), which is why the device now claims `tunnel.gre: [ ipv4, ipv6 ]`. GRE in a transport VRF is still unclaimed and unrun. |
| `28-files-maxprefix.yml` | bgp, files plugin | `files` escape-hatch worked example: raw FRR `maximum-prefix 100` configlet injected and applied (frr fallback + our own `deploy-config/sonic-clab.yml`, same mechanism proven for tunnel.gre/evpn.multihoming) |
| `29-bgp-domain.yml` | bgp, ospf, bgp.domain plugin | isolated iBGP domains: s1 keeps exactly 2 (red) peers, the cross-domain s1<->s4 session is pruned, s3 (red) reaches h2 via RR reflection, s4 (blue) has zero BGP neighbors and no route to h2 (frr fallback + netlab-core plugin logic, no device template involved) |

Run with `netlab up <file>` from this directory (needs `docker-sonic-vs:latest` pulled locally
and a `multilab` id that isn't in use -- these default to id 52).

## What actually needed a `sonic-clab.j2` override vs. what's free

Most modules above render and deploy with **zero new template files** -- they fall through
automatically to the package's `<module>/frr.j2` via the `ansible_network_os: frr` search-path
fallback (see `netsim/devices/sonic.yml`'s `group_vars.ansible_network_os` inherited from
the parent `sonic` device). Only these needed a real `sonic-clab.j2`, all ported (mostly
verbatim) from our out-of-tree reference and re-verified live here:

* `initial` -- interface/loopback/VLAN/PortChannel bring-up via the `config` CLI, CONFIG_DB->
  kernel sync for routed ports, FRR daemon enable.
* `bgp` -- `no router bgp` reset wrapper (deploy-config strips the reset line on first apply).
* `vlan` -- switchport membership via `config vlan member add`, **plus a kernel bridge sync**
  (docker-sonic-vs's `vlanmgrd` races at boot and often never mirrors `VLAN_MEMBER` rows written
  during the first deploy -- found live: config_db had the row, but the kernel port had no
  `master Bridge` and cross-node ping failed 100% until the sync was added).
* `lag` -- PortChannel members via `config portchannel member add`, plus a defensive `teamdctl`
  sync (teammgrd is more reliable than vlanmgrd but the out-of-tree reference added this
  belt-and-suspenders step anyway).
* `vrrp.version` -- FRR vrrpd v2/v3 pin via `vtysh -f` (this one lives under
  `netsim/extra/vrrp/version/sonic-clab.j2`, not `netsim/ansible/templates/` -- it's implemented
  as a node_config plugin with its own search path, discovered by a `netlab create` failure:
  "Cannot find vrrp.version configuration template").
* `vxlan` -- EVPN-VXLAN L2VNI/L3VNI built directly on the kernel/FRR path (bridge + kernel vxlan
  netdev), since docker-sonic-vs's orchagent can't program the VXLAN dataplane from CONFIG_DB.

`evpn`, `evpn.multihoming`, `isis`, `vrf`, `mpls`, `sr`, `srv6`, `bfd`, `gateway`, `routing`,
`ripv2`, `tunnel.gre`, `bgp.session`/`bgp.policy`/`bgp.originate`/`bgp.domain`/`ebgp.multihop`,
`ospf.areas`, and the IPv4/IPv6 dual-stack variants of all of the above need no override at
all -- they render via the frr fallback too.

One bug found and fixed by `27-tunnel-gre.yml`: the `initial/sonic-clab.j2` bash/config_db loop
deliberately skips tunnel interfaces (the `config` CLI only accepts Ethernet/PortChannel/
Vlan/Loopback names), and the shared `tunnel.gre/frr.j2` plugin template only creates the kernel
netdev (`ip tunnel add`), it doesn't address it -- so the tunnel interface was created with NO
IP at all until the vtysh heredoc in `initial/sonic-clab.j2` was extended to address
tunnel-type interfaces itself (the same thing the vanilla `frr` device's own initial template
already does for every interface, unconditionally -- our SONiC template only omits it elsewhere
because config_db handles addressing for every other interface type).

Genuinely unsupported on this image, not just untested (see `netsim/devices/sonic.yml`'s
`support.caveats` for the live-probed reasons): DHCP relay/client, real STP port-blocking,
the `mlag.vtep` active-active datapath, and `check.config` (together with the `files` plugin --
a netlab-core plugin-ordering bug, not device-specific).

This is a device bring-up smoke-test set, not (yet) wired into netlab's shared per-module
`tests/integration/<module>/NN-*.yml` parameterized matrix that runs across all devices -- that
wiring is tracked as a follow-up.
