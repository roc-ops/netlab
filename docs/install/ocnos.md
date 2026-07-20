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
