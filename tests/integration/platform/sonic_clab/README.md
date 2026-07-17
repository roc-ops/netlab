# SONiC (clab) smoke tests -- issue #40

Device-specific bring-up smoke tests for the `sonic_clab` device (docker-sonic-vs on
containerlab). Every topology below has been run live against a real `docker-sonic-vs:latest`
node (containerlab 0.77.0, multilab id 52) with `netlab up`/`netlab initial`, all over Ansible's
`docker` connection plugin -- no SSH. Adapted from our out-of-tree reference implementation
(roccontlab netlab/sonic/) with `device: sonic` -> `device: sonic_clab` and `multilab.id` -> 52.

| topology | module(s) exercised | what was checked live |
|---|---|---|
| `01-initial.yml` | initial | 1-node bring-up: hostname, loopback IP, interfaces |
| `02-ospf.yml` | ospf | adjacency Full (pure `ansible_network_os: frr` fallback, no `sonic_clab` template) |
| `03-bgp.yml` | bgp | eBGP session Established (`bgp/sonic_clab.j2` override) |
| `04-isis.yml` | isis | adjacency Up (frr fallback) |
| `05-vrf.yml` | vrf, ospf | per-VRF OSPF adjacency Full (frr fallback) |
| `06-mpls-sr-l3vpn.yml` | isis, bgp, mpls, sr, vrf | LDP Operational, real kernel MPLS label FIB, VPNv4 Established, L3VPN ping 0% loss. **Needs a one-time host prereq: `sudo modprobe mpls_router mpls_iptunnel`** |
| `07-srv6.yml` | isis, srv6 | real kernel `seg6local` End/End.X routes, cross-node ISIS locator advertisement |
| `08-vlan.yml` | vlan | IRB SVI + kernel bridge datapath, 0% loss (needed a `sonic_clab.j2` override + kernel-sync -- see below) |
| `09-lag.yml` | lag, ospf | LACP aggregate (teamd), OSPF Full over the PortChannel (needed a `sonic_clab.j2` override) |
| `10-vrrp-version.yml` | gateway, vrrp.version plugin | VRRP pinned to v2, Master state confirmed (template lives in `netsim/extra/vrrp/version/sonic_clab.j2`, NOT `netsim/ansible/templates/` -- it's a node_config plugin, different search path) |
| `11-vxlan-evpn.yml` | vlan, ospf, bgp, vxlan, evpn | L2VNI up, remote VTEP learned, h1<->h2 ping across the tunnel 0% loss (needed a `sonic_clab.j2` override) |
| `12-evpn-irb-l3vni.yml` | vlan, vrf, ospf, bgp, vxlan, evpn | symmetric-IRB L3VNI inter-subnet ping 0% loss |
| `13-vrf-leak.yml` | vrf, bgp | VRF red's prefix leaks into VRF blue's RIB via BGP RT import |
| `14-bgp-session.yml` | bgp, bgp.session plugin | MD5 password applied, session Established (frr fallback) |
| `15-bgp-policy.yml` | bgp, bgp.policy plugin, routing | route-map locpref/weight/community all applied (frr fallback) |
| `16-ebgp-multihop.yml` | bgp, ebgp.multihop plugin, ospf, routing | loopback-to-loopback multihop session Established (frr fallback) |
| `17-ospf-areas.yml` | ospf, ospf.areas plugin | stub area adjacency Full (frr fallback) |
| `18-bgp-originate.yml` | bgp | originated prefix received by the eBGP peer (frr fallback) |
| `19-bfd.yml` | ospf, bfd | BFD session Up under OSPF (frr fallback; needed `features.ospf.bfd`/`features.bgp.bfd` device flags) |
| `20-ripv2.yml` | ripv2 | route learned via RIP (frr fallback) |
| `21-evpn-multihoming.yml` | lag, vlan, ospf, bgp, vxlan, evpn, evpn.multihoming plugin | Ethernet Segment on PortChannel1, dual-homed h1<->h2 ping 0% loss (needs the `h1-fixaddr/` custom-config addon, a generic `linux`-device host-addressing workaround, not SONiC-specific) |

Run with `netlab up <file>` from this directory (needs `docker-sonic-vs:latest` pulled locally
and a `multilab` id that isn't in use -- these default to id 52).

## What actually needed a `sonic_clab.j2` override vs. what's free

Most modules above render and deploy with **zero new template files** -- they fall through
automatically to the package's `<module>/frr.j2` via the `ansible_network_os: frr` search-path
fallback (see `netsim/devices/sonic_clab.yml`'s `group_vars.ansible_network_os` inherited from
the parent `sonic` device). Only these needed a real `sonic_clab.j2`, all ported (mostly
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
  `netsim/extra/vrrp/version/sonic_clab.j2`, not `netsim/ansible/templates/` -- it's implemented
  as a node_config plugin with its own search path, discovered by a `netlab create` failure:
  "Cannot find vrrp.version configuration template").
* `vxlan` -- EVPN-VXLAN L2VNI/L3VNI built directly on the kernel/FRR path (bridge + kernel vxlan
  netdev), since docker-sonic-vs's orchagent can't program the VXLAN dataplane from CONFIG_DB.

`evpn` and `evpn.multihoming` need no override at all -- they render via the frr fallback too.

This is a device bring-up smoke-test set, not (yet) wired into netlab's shared per-module
`tests/integration/<module>/NN-*.yml` parameterized matrix that runs across all devices -- that
wiring is tracked as a follow-up.
