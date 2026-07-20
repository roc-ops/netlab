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

## Validation caveat (important for `netlab validate`)

OcNOS's `ocnos` user has a **restricted shell**: it drops into `cmlsh` only on an *interactive* login.
`ssh ocnos@node "show ..."` (and every `cmlsh -e/-c` variant) returns ``Try `cmlsh --help'`` — there is
**no non-interactive exec** — and OcNOS emits **no JSON** (it is OpenConfig/gNMI/NETCONF-native).
netlab's native `netlab validate` runs `show ... json` over `netlab connect` (SSH), so it **cannot
validate OcNOS unmodified**. Validate instead over the working transports:

* **gNMI** — subscribe-once (Get times out on the vrnetlab VM); `gnmic` against the OpenConfig paths.
* **Ansible** — `ipinfusion.ocnos.ocnos_command` for assertion-based show checks.

This is a netlab-framework limitation (validation transport), not an OcNOS config-template issue —
config generation (`netlab create`/`up`) works normally.
