# netlab validation-transport contribution — design + verdict

## Problem
`netlab validate` gets DUT operational state from exactly three sources (`netsim/cli/validate/tests.py`
dispatch, ~L145-156): `show`/`exec` -> `devices.get_parsed_result`/`get_result_string` -> **`connect_to_node`
(netlab connect = SSH for every method: ssh/network_cli/netconf/httpapi)**, and `suzieq` ->
`suzieq.get_result` (a separate container source). OcNOS/ArcOS can't be reached by any:
- OcNOS SSH = restricted `cmlsh` (no non-interactive show, no JSON); gNMI partial (OSPF not exposed).
- ArcOS CLI `show` hangs on 8.2.1A; gNMI-native.
- Both are vrnetlab VMs, so docker-exec (what makes SONiC work) reaches the wrapper, not the CLI.

So no per-device validation plugin can work unmodified -- the fix is a new **validation transport**, exactly
the shape of the existing `suzieq` source.

## Design (recommended option A -- a new source action, mirroring SuzieQ)
Add `ansible` (and later `gnmi`) as validation **actions**, peers of `show`/`exec`/`suzieq`:

1. `netsim/cli/validate/ansible.py` -- `get_result(v_entry,n_name,topology,verbosity)`: run the show command on
   the node via **ansible** (`ipinfusion.ocnos.ocnos_command` for OcNOS) using the lab inventory netlab already
   generates, capture stdout, parse (JSON if the show emits it, else the plugin `valid_` screen-scrapes).
   Structured identically to `suzieq.get_result`.
2. `netsim/cli/validate/gnmi.py` -- same shape, runs `gnmic -a <mgmt> get <path>` -> OpenConfig JSON (ArcOS, and
   OcNOS where exposed). The OOT validators (`_archive-oot-devices/ocnos/validate/{ocnos.py,ocnos_gnmi.py}`)
   already hold the exact commands/paths + parsing to port.
3. Hook: in `tests.py` add `elif action == 'ansible': result = ansible.get_result(...)` (and `gnmi`); teach
   `utils.find_test_action`/`plugin.find_plugin_action` to recognise `ansible`/`gnmi` when a `validate:` entry or
   a device plugin provides them. A device declares its default validation transport via
   `defaults.devices.<dev>.netlab_validate.transport: ansible|gnmi` so the standard tests route automatically.

Option B (smaller, transparent): route the *existing* `show`/`exec` through ansible/gnmi when the device sets
that transport -- changing only `get_parsed_result`/`get_result_string`. Cleaner for test topologies (no
`ansible:` keys) but overloads the existing actions. **Recommend A** -- it matches the SuzieQ precedent the
maintainers already accepted, keeps the transport explicit, and is purely additive (zero risk to existing devices).

## Upstream acceptability
High. SuzieQ proves netlab accepts non-SSH validation sources; this is the same extension point, additive, with
no change to any existing device or test. General value (any OpenConfig/gNMI NOS becomes validatable) is a strong
upstream selling point.

## Effort to reach "full"
- Framework: the `ansible.py`/`gnmi.py` sources + the ~10-line `tests.py`/`find_test_action` hook -- **small**.
- Per-device plugins: `netsim/validate/{ospf,bgp,isis,vrf,...}/{ocnos,arcos}.py` `show_`/`valid_` pairs -- the
  bulk, but the OOT validators already encode the commands+parsing; **moderate, mechanical**.
- OcNOS: ansible-cmlsh transport covers all modules (text parse) + gNMI where exposed. ArcOS: gnmi transport.
- **Verdict: YES -- this single, additive framework change gets BOTH OcNOS and ArcOS to full, no core redesign.**
  The one gating cost is a reliable OcNOS/ArcOS boot loop to develop the per-module parsers against.
