# OcNOS → full netlab device: validation transport plan

## Question answered
**Is OcNOS-`full` (native `netlab … --validate`) achievable without a netlab framework change?**
**Partly.** OcNOS can pass every **interoperability / probe-validated** integration test *today* with no
core change; reaching support for **DUT-side** validation tests needs a small framework addition.

## How netlab validation actually works (verified in `netsim/cli/validate/`)
A `validate:` entry runs an **action** to fetch data, then asserts with a `valid:` expression or `plugin:`:
- `show` → runs the command over **`netlab connect`**, expects **parseable JSON**.
- `exec` → runs over `netlab connect`, returns raw stdout.
- `suzieq` → queries the SuzieQ tool (a **non-SSH** data source) — the key precedent.
- `plugin` → device plugin `validate/<dev>.py`, but still requires a `show_`/`exec_` action (runs over connect).

`netlab connect` (`cli/connect.py`) maps **every** method — `ssh`/`network_cli`/`netconf`/`httpapi` — to **SSH**;
only `docker` is separate. So validation data comes from **SSH-CLI, SuzieQ, or docker-exec** — nothing else.

## Why OcNOS can't validate DUT-side over `netlab connect`
- OcNOS `netlab_console_connection: ssh` → SSH → restricted **`cmlsh`**: **rejects non-interactive `show`** and emits **no JSON**. So `show`/`exec` actions fail on the OcNOS DUT.
- It is a **vrnetlab VM inside a container**, so `docker-exec` (the SONiC trick) reaches the wrapper Linux, not the OcNOS CLI.
- **gNMI is partial**: interface/routing via OpenConfig/`ipi:` (Get times out → must Subscribe once), and **OSPF is not exposed via gNMI at all** (per the team's `ocnos_gnmi.py`). So gNMI alone cannot validate routing protocols.
- The only complete OcNOS state source is **ansible `ocnos_command`** (network_cli via the `ipinfusion.ocnos` collection) — returns **CLI text** (not JSON), needs per-command parsing. This is what the OOT `validate/ocnos.py` uses.

## The lever that works with NO core change: probe-based tests
Most netlab integration tests validate the DUT **indirectly, on the FRR/EOS probes**. Example
`tests/integration/ospf/ospfv2/01-network.yml`: every entry is `nodes: [x1,x2]` (FRR),
`plugin: ospf_neighbor(nodes.dut.ospf.router_id)` / `ospf_prefix(...)` — the probes assert they see the
OcNOS DUT as a neighbor and route through it. **OcNOS passes these with zero OcNOS-side validation code** —
it just has to configure the protocol correctly (which it does; `netlab create/initial` verified).

→ **Action:** run the suite and mark every test whose `validate:` entries target only probe nodes as
**passing now**. That alone lifts OcNOS from "config-only" to "interop-validated" — legitimately upstreamable.

## For `full` (DUT-side tests): the framework change
Some tests assert state **on the DUT** (`nodes: [dut]`). Those need an OcNOS validation transport. The clean,
**precedented** design is a new validation action modeled on `suzieq` (`cli/validate/suzieq.py`):

- Add `cli/validate/ocnos_cli.py` (or a generic `ansible`/`gnmi` action) + register it in
  `utils.find_test_action` (the `(show,exec,config,suzieq)` tuple) and the `tests.py` dispatch.
- It runs the show via **`ansible ocnos_command`** (reuse the OOT `validate/ocnos.py` + `ocnos_gnmi.py` — they
  already know the commands/paths and the text/JSON parsing), returns parsed data as `_result`; standard
  `valid:` expressions then work.
- This is a **netlab-core PR, separate from `device/ocnos`** — it also unlocks any gNMI/ansible-native NOS
  (ArcOS, future). Effort: ~one `suzieq.py`-sized module + per-module `valid_*` parsers for the DUT-side checks.

## Per-module status (to fill in from the suite run)
| module | probe-validated (pass now) | DUT-side checks (need transport) |
|---|---|---|
| ospf/ospfv2/v3, bgp, isis (adjacency+reachability) | yes (probes) | route-detail/timers on DUT |
| vrf, vlan, lag, gateway | mixed | DUT interface/vrf state |

## Recommendation
1. **Now, no core change:** run the suite, record probe-validated passes, ship OcNOS at the honest level.
2. **For full:** land the `suzieq`-style **ansible/gNMI validation action** as a netlab-core contribution, then
   add per-module `valid_*` parsers. That is the single upstream lever for OcNOS **and** ArcOS full validation.
