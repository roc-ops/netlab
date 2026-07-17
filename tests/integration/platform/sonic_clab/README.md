# SONiC (clab) smoke tests -- issue #40

Device-specific bring-up smoke tests for the `sonic_clab` device (docker-sonic-vs on
containerlab), verified live against a real `docker-sonic-vs:latest` node:

* `01-initial.yml` -- 1-node bring-up: `netlab up` + `netlab initial`, confirms hostname,
  loopback IP, and interface bring-up land via Ansible's `docker` connection plugin (no sshd).
* `02-ospf.yml` -- 2-node OSPF adjacency (module template is the package `ospf/frr.j2`,
  reached automatically through the `ansible_network_os: frr` fallback -- no `sonic_clab`
  override needed).
* `03-bgp.yml` -- 2-node eBGP session (`bgp/sonic_clab.j2` override: SONiC always needs a
  `no router bgp` reset before re-declaring the peer; `deploy-config/sonic_clab.yml` strips that
  line on first apply, since FRR 10.5's mgmtd on docker-sonic-vs rejects resetting a protocol
  that was never configured).

Run with `netlab up <file>` from this directory (needs `docker-sonic-vs:latest` pulled locally
and a `multilab` id that isn't in use -- these default to id 52).

This is a device bring-up smoke test, not (yet) wired into netlab's shared per-module
`tests/integration/<module>/NN-*.yml` parameterized test matrix that runs across all devices --
that wiring, plus porting the remaining FRR-delegated modules (isis, vrf, vxlan, evpn, ...) is
tracked as a follow-up increment.
