# OcNOS integration test

`ospf-bgp.yml` -- minimal two-node OcNOS smoke test (OSPFv2 + iBGP over one
point-to-point link). Exercises the `ocnos` device definition
(`netsim/devices/ocnos.yml`), the `initial`/`ospf`/`bgp` templates, and the
`ipinfusion.ocnos` + `ansible.netcommon.network_cli` config-push path
(`netsim/ansible/tasks/deploy-config/ocnos.yml`).

Requires the `ipinfusion.ocnos` Ansible collection and a real OcNOS clab image
(commercial NOS -- users provide their own, see `netsim/devices/ocnos.yml` clab
image reference):

```
ansible-galaxy collection install ipinfusion.ocnos
export ANSIBLE_COLLECTIONS_PATH=~/.ansible/collections:$(python -c "import netsim,os;print(os.path.dirname(netsim.__file__))")/../ansible_collections

netlab up tests/integration/platform/ocnos/ospf-bgp.yml
```

## Status: re-run live 2026-08-29 (issue #87)

Three separate claims, as everywhere in `tests/integration/platform/` -- does it CREATE, does
it DEPLOY, does its stated result HOLD:

| creates | deploys | result |
|---|---|---|
| Y | Y | Y, **dual-stack** |

Verified on `vrnetlab/ipinfusion_ocnos:7.0.0-262` with the `ipinfusion.ocnos` collection
installed: `netlab up` completes, and the device reports **OSPFv2 Full, OSPFv3 Full, BGP
Established on the IPv4 AF (10.8.0.2) and BGP Established on the IPv6 AF
(2001:db8:80:2::1)**, one prefix received on each.

The IPv6 half is new information, not a change in behaviour. The topology declares no IPv6
addressing of its own, but netlab's default pools are dual-stack, so this lab has always built
an IPv6 OSPF adjacency and an IPv6 BGP session -- checking only `show ip ospf neighbor` and
`show ip bgp summary`, as the commands below used to, was always half a test.

Verify: OSPF adjacency Full and BGP session Established on **both** address families, e.g.

```
ansible -i hosts.yml r1 -m ipinfusion.ocnos.ocnos_command -a 'commands="show ip ospf neighbor"'
ansible -i hosts.yml r1 -m ipinfusion.ocnos.ocnos_command -a 'commands="show ip bgp summary"'
ansible -i hosts.yml r1 -m ipinfusion.ocnos.ocnos_command -a 'commands="show ipv6 ospf neighbor"'
ansible -i hosts.yml r1 -m ipinfusion.ocnos.ocnos_command -a 'commands="show bgp ipv6 summary"'
```

The topology now loads the `multilab` plugin (id 53) so it does not land on the shared default
management network alongside whatever else is running. Override with
`-s defaults.multilab.id=<n>`. Note that `-s multilab.id=<n>` *without* the plugin loaded is a
hard error rather than merely inert -- the same class of silently-or-loudly-ineffective
declaration that #86 found in `netlab/labs/smoke-arcos-sonic/topology.yml`.

This topology intentionally has no `validate:` block -- see the comment in
`ospf-bgp.yml` for why (OcNOS's cmlsh restricted shell has no non-interactive
exec mode, so netlab's native device-side `show`-command validation path does
not apply; the existing generic module suites in `tests/integration/ospf/` and
`tests/integration/bgp/` validate a device-under-test via probe-node plugin
checks instead, and run unmodified against `-d ocnos`).
