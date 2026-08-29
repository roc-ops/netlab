# SONiC (clab) smoke tests -- issue #40

Device-specific bring-up smoke tests for the `sonic` device under provider `clab`
(docker-sonic-vs on containerlab). Adapted from our out-of-tree reference implementation
(roccontlab netlab/sonic/) with `multilab.id` -> 52.

## How to read this suite

Every row states **three separate claims**, and they are never allowed to stand in for one
another:

| column | question | what proves it |
|---|---|---|
| **creates** | does `netlab create` succeed? | seconds, no containers, no device |
| **deploys** | does `netlab up` complete and the config actually apply on the device? | a real container, minutes |
| **result** | does the thing the row claims actually hold on the running lab? | reading protocol or datapath state off the device |

They come apart. Issue #86 found a row recorded as "passed live, 0% loss" that could not be
**created**; fixing that let it reach a deploy that then failed for a second, unrelated reason.
A row that creates is not a row that passes.

## Status: the whole suite was re-run live on 2026-08-29 (issue #87)

**All 29 topologies were run through `netlab create`, deployed with `netlab up`, and had their
stated result checked against real device output** -- one lab at a time on
`docker-sonic-vs:latest` under containerlab, on the converged `sonic` device at roc/lab
c397a7045a. There are no unrun rows left in this suite and no rows carried forward "in
expectation". Where a row's result is partial, the table says which half holds and which does
not, rather than marking the row green or deleting the claim.

What that re-run changed:

* **26 of 29 rows hold in full.** Several are stronger than they were recorded: `06` also has a
  working **VPNv6** datapath, `11` also passes **IPv6** across the L2VNI, and `02`/`17`/`22` are
  green on **both** address families. Those results were simply never recorded before.
* **`16-ebgp-multihop` was broken and is now fixed.** Its statics were hardcoded to addresses
  from a netlab pool that has since moved, so both multihop sessions sat in `Active` forever.
  Rewritten symbolically; verified Established on both AFs.
* **`21-evpn-multihoming` was broken and is half-fixed.** Its own workaround file hardcoded an
  address from the same moved pool, which put the two hosts on different subnets and made the
  recorded 0% loss impossible. That part is fixed and verified. The **dual-homing itself does
  not form** -- see the row.
* **`20-ripv2` is half-dead and was recorded as green.** The IPv4 half works. The IPv6 half
  renders `router ripng`, is accepted by vtysh, and does nothing, because `ripngd` is never
  started.
* **`10-vrrp-version` is confirmed still stuck**, exactly as #86 corrected it, with one new
  piece of evidence narrowing it further.

Three rows out of six were wrong when #86 sampled them. Re-running all 29 found three more
problems (`16`, `20`, `21`) that no amount of reading would have surfaced. That ratio is the
argument for the cadence written down in [`../README.md`](../README.md).

**These are container tests, despite the directory name.** The directory is named for the
DEVICE (`sonic`), because that is what netlab's integration-test runner keys on, but every
topology in it pins `provider: clab` and exercises the monolithic `docker-sonic-vs` image.
None of them says anything about the libvirt SONiC VM, which shares the device but is a
different architecture (multi-container, FRR reached through `docker exec bgp vtysh`) and
is untested. Read a green result here as the container works, never as the device works.

These tests were written against a separate `sonic_clab` device, which was retired in favour
of upstream's single-device shape (issue #86): the container now lives in the `clab:` block
of `netsim/devices/sonic.yml` and its templates are named `<module>/sonic-clab.j2`.

### Legend

`Y` = verified on 2026-08-29 against real device output. `PARTIAL` = the row's claim splits;
the result column says exactly which half was proven and which was disproven.

| topology | module(s) | creates | deploys | result (verified 2026-08-29) |
|---|---|---|---|---|
| `01-initial.yml` | initial | Y | Y | Y -- hostname `s1`; `Loopback0` carries 10.8.0.1/32 and 2001:db8:80:1::1/64; mgmt up |
| `02-ospf.yml` | ospf | Y | Y | Y -- OSPFv2 Full **and** OSPFv3 Full. Dual-stack green; previously recorded as a single "Full" |
| `03-bgp.yml` | bgp | Y | Y | Y -- eBGP Established on both AFs, 1 prefix received each way on each |
| `04-isis.yml` | isis | Y | Y | Y -- L2 adjacency Up; the remote loopback is installed in both the IPv4 and the IPv6 RIB |
| `05-vrf.yml` | vrf, ospf | Y | Y | Y -- OSPFv2 Full inside VRF red (`show ip ospf vrf all neighbor`); default VRF correctly has no OSPF. OSPFv3-in-VRF is row 26 |
| `06-mpls-sr-l3vpn.yml` | isis, bgp, mpls, sr, vrf | Y | Y | Y, in full and then some -- IS-IS Up, LDP OPERATIONAL, **VPNv4 and VPNv6** Established, a real kernel MPLS label FIB (`ip -M route`: LDP transport, IS-IS SR and BGP VPN labels), red->red **ping 5/5 and ping6 4/4** at 0% loss across two labels, and the by-design one-way blue->red failure reproduces (`% Network not in table` on s1). The VPNv6 datapath had never been recorded. **Needs a one-time host prereq: `sudo modprobe mpls_router mpls_iptunnel`** |
| `07-srv6.yml` | isis, srv6 | Y | Y | Y **as a control-plane and kernel-state claim, which is all it claims** -- real `seg6local` End (uN) and End.X (uA) routes on both nodes, cross-node locator advertisement confirmed in the IS-IS LSDB *and* as an installed remote-locator route (s1 has 5f00:0:2::/48 via IS-IS, s2 has 5f00:0:1::/48). **No traffic was ever sent through an SRv6 SID**; this row does not claim an SRv6 datapath and must not be read as one |
| `08-vlan.yml` | vlan | Y | Y | Y -- IRB SVI + kernel bridge datapath, s1<->s2 IPv4 4/4 and IPv6 3/3, 0% loss. Needs a `sonic-clab.j2` override + kernel-sync, see below |
| `09-lag.yml` | lag, ospf | Y | Y | Y -- LACP aggregate (teamd), **both** members `selected: yes` on both nodes, OSPF Full over `PortChannel1` |
| `10-vrrp-version.yml` | gateway, vrrp.version plugin | Y | Y | **PARTIAL.** The version pin holds: `Protocol Version 2`. The protocol does **not** run: `Status (v4)` and `Status (v6)` are both `Initialize`, 0 state transitions, 0 advertisements sent -- identically on the retired `sonic_clab` device, so a stale claim rather than a regression. **New evidence (2026-08-29), narrowing it further than #86 did**: vrrpd's own `Primary IP (v4)` field is *empty*, although zebra sees `172.31.43.1/24` on the parent `Ethernet0` and the `vrrp-0-1` macvlan is UP with the VRRP MAC and the VIP. So the kernel and zebra are both healthy and vrrpd never bound a primary address. Still inside vrrpd; still out of scope here. (Template lives in `netsim/extra/vrrp/version/sonic-clab.j2`, NOT `netsim/ansible/templates/` -- node_config plugin, different search path.) |
| `11-vxlan-evpn.yml` | vlan, ospf, bgp, vxlan, evpn | Y | Y | Y, plus an AF that was never recorded -- EVPN Established, L2VNI 10043 up with 1 remote VTEP, h1<->h2 across the tunnel **IPv4 5/5 and IPv6 4/4**, 0% loss |
| `12-evpn-irb-l3vni.yml` | vlan, vrf, ospf, bgp, vxlan, evpn | Y | Y | Y -- L2VNI 10010 + L3VNI 10099 in VRF `tenant`, inter-subnet h1->h2 5/5 0% loss, remote prefixes installed `via l3vni10099 onlink`. This row **failed to deploy** when #86 re-ran it and was fixed there (the IPv6 branch of the initial template lacked the VNI-backed-SVI guard) |
| `13-vrf-leak.yml` | vrf, bgp | Y | Y | Y -- red's prefix appears in blue's RIB via RT import (`B>* ... is directly connected, red (vrf red)`), and red does **not** get blue's |
| `14-bgp-session.yml` | bgp, bgp.session plugin | Y | Y | Y -- MD5 password applied to the IPv4 **and** IPv6 neighbours, both sessions Established |
| `15-bgp-policy.yml` | bgp, bgp.policy plugin, routing | Y | Y | Y -- all four effects confirmed on the received prefix: `localpref 250`, `weight 50`, `Community: 65001:100`, and `metric 111` seen on the far side from the auto-generated outbound map. IPv6 route-maps render and the weight applies on IPv6 too |
| `16-ebgp-multihop.yml` | bgp, ebgp.multihop plugin, ospf, routing | Y | Y | **Was BROKEN, now Y (fixed in this tree).** As recorded, both multihop sessions sat in `Active` forever: the topology hardcoded statics to `10.0.0.2/32 via 10.1.0.2`, netlab's pools have since moved to 10.8.0.0/24 and 10.10.0.0/30, so the next hop was on no interface and the static never even entered the RIB. It was also half-blind -- the plugin builds an IPv4 *and* an IPv6 session, and only IPv4 statics were ever provided. Rewritten symbolically (`node:` / `nexthop.node:`), which cannot rot with the pools and covers both AFs. Verified: **both AFs Established**, statics installed, 1 prefix each way |
| `17-ospf-areas.yml` | ospf, ospf.areas plugin | Y | Y | Y -- `Area ID: 0.0.0.1 (Stub)` with 2 areas attached, adjacency Full in **both** OSPFv2 and OSPFv3 |
| `18-bgp-originate.yml` | bgp | Y | Y | Y -- the originated 10.201.9.0/24 is received by the eBGP peer |
| `19-bfd.yml` | ospf, bfd | Y | Y | Y -- **two** BFD sessions up per node (IPv4 and link-local IPv6) under OSPF, OSPF Full |
| `20-ripv2.yml` | ripv2 | Y | Y | **PARTIAL, and the missing half was recorded as passing.** IPv4 holds: `R>* 10.8.0.2/32 [120/2]` learned via RIP. IPv6 is **dead**: the template renders `router ripng` with networks, vtysh accepts it, and `show ipv6 route ripng` is empty -- because `/etc/frr/daemons` has `ripngd=no` and the daemon never starts. Config that looks applied and does nothing. Cause is one line in a **device file**, out of this suite's scope to change: `netsim/devices/sonic.yml` maps `ripv2: [ ripd ]` where upstream `netsim/devices/frr.yml` has `ripv2: [ ripd, ripngd ]` |
| `21-evpn-multihoming.yml` | lag, vlan, ospf, bgp, vxlan, evpn, evpn.multihoming plugin | Y | Y | **PARTIAL -- two independent defects, one fixed here, one not.** (a) FIXED: `h1-fixaddr/linux.j2` hardcoded `172.16.0.5/24` while the red VLAN is now 172.18.0.0/24, so the two hosts were on different subnets and the recorded 0% loss was impossible -- silently, because the address still applied cleanly. Now derived from the VLAN's own prefix; h1<->h2 verified **5/5, 0% loss**. (b) NOT FIXED, and it makes the word "dual-homed" wrong: **the LAG is single-homed in practice.** s2 reports the Ethernet Segment `State: down`, `DF status: non-df`, its `PortChannel1` is NO-CARRIER, and h1's bond shows `Number of ports: 1` in the active aggregator. s1 and s2 present **different LACP system MACs** (`1e:57:e1:d5:cf:38` vs `2a:db:d6:46:59:db`), so an LACP host can only ever aggregate with one of them; the rendered `evpn.multihoming` script sets `evpn mh es-id` and never programs a shared LACP system-id. The EVPN control plane is genuinely healthy (ESI advertised, s1 elected DF, s2 sees the remote VTEP with `df_pref`) -- so read this row as **ES + EVPN control plane proven, dual-homing NOT proven, datapath proven over a single leg**. Needs the `h1-fixaddr/` addon (a generic `linux`-device host-addressing workaround, not SONiC-specific) |
| `22-ospfv3.yml` | ospf | Y | Y | Y -- both AFs Full, **ping 4/4 and ping6 4/4** across the loopbacks |
| `23-isis-v6-bgp-v6.yml` | isis, bgp | Y | Y | Y -- IS-IS Up with the IPv6 topology installed, iBGP IPv6 AF Established over the loopbacks |
| `24-routing-v6.yml` | routing | Y | Y | Y -- IPv6 static installed (`S>* 2001:db8:143:42::/64 via 2001:db8:100::2`) and the IPv6 prefix-list present with its `ge 64 le 64`. Note this row uses the **symbolic** `nexthop.node:` form and therefore did NOT rot when the pools moved -- the direct contrast with row 16 |
| `25-vrf-v6-leak.yml` | vrf, bgp | Y | Y | Y -- red's IPv6 prefix appears in blue's v6 RIB via RT import; red does not get blue's |
| `26-vrf-v6-ospfv3.yml` | vrf, ospf | Y | Y | Y -- OSPFv3 Full **inside VRF red** specifically (`show ipv6 ospf6 vrf red neighbor`); the default VRF has no OSPFv3 instance at all, which is what makes it a VRF result rather than a leak |
| `27-tunnel-gre.yml` | ospf, tunnel.gre plugin | Y | Y | Y -- a real `link/gre` netdev (`gre remote 10.10.0.2 local 10.10.0.1 dev Ethernet0`, mtu 1476) addressed on both AFs, **ping 5/5 and ping6 4/4** across the tunnel. This is the row from #87's title: it could not be **created** (the `tunnel.gre` flag was a bool where the plugin tests list membership) and then could not **deploy** (the IPv6 branch of the initial template lacked the tunnel guard), both fixed in #86. GRE in a transport VRF is still unclaimed and unrun |
| `28-files-maxprefix.yml` | bgp, files plugin | Y | Y | Y -- the raw FRR configlet lands in the running config (`neighbor 10.10.0.2 maximum-prefix 100`) and the session is Established |
| `29-bgp-domain.yml` | bgp, ospf, bgp.domain plugin | Y | Y | Y, including the datapath -- s1 keeps exactly 2 (red) peers, the cross-domain s1<->s4 session is pruned, s3 has h2's prefix by RR reflection and **pings h2 3/3**, and s4 (blue) has zero BGP neighbours (`% No BGP neighbors found in VRF default`) and no route to h2 (`% Network not in table`, ping `Network is unreachable`) |

Run with `netlab up <file>` from this directory (needs `docker-sonic-vs:latest` pulled locally
and a `multilab` id that isn't in use -- these default to id 52).

### Two things worth knowing before you re-run this suite

**Do not trust `ml_autoid` to pick your id here.** It reads running `clab-ml-<id>-*` containers
and its own reservation directory, but not netlab's own instance registry, so the two can
disagree: on 2026-08-29 it allocated id 2, which `netlab up` then refused because netlab
considered instance 2 to belong to another lab's directory. It failed safe -- but pin an id you
have checked against `netlab status --all` rather than relying on the allocator.

**Read convergence at the same moment as the result.** Every result above was read after the
lab settled; an overlay caught mid-convergence produces a false FAILURE just as readily as a
stale README produces a false pass.

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
