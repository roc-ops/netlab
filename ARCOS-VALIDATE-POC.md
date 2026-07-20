# ArcOS native validation PoC — verdict

**Question:** can ArcOS reach *full* netlab validation the **SONiC way** — native
`netlab up -d arcos <test> --validate` passing over **docker-exec → confd_cli** — WITHOUT the
framework validation-transport (ansible/gNMI) work that OcNOS needs?

**Verdict: YES.** ArcOS is a native containerlab container whose CLI (`confd_cli`, ConfD /
OpenConfig-modeled) is reachable over the *same* `ansible_connection: docker` path used to deploy
config. netlab's stock validation framework drives it with a per-device `netlab_show_command`; no
core changes, no gNMI, no SSH (all three are disabled on this build).

## Live result (clean end-to-end, fresh boot)

```
$ netlab up -d arcos tests/integration/platform/arcos/02-ospf.yml --validate
...
[adj_r1]  r1 has r2 as a FULL OSPFv2 neighbor (docker-exec confd_cli) [ node(s): r1 ]
[PASS]    r1: ArcOS OSPFv2 neighbor 10.0.0.2 is FULL
[PASS]    Test succeeded in 40.2 seconds
[adj_r2]  r2 has r1 as a FULL OSPFv2 neighbor (docker-exec confd_cli) [ node(s): r2 ]
[PASS]    r2: ArcOS OSPFv2 neighbor 10.0.0.1 is FULL
[SUCCESS] Tests passed: 2
```

## Mechanism (how the stock framework reaches confd_cli)

netlab's validation core (`netsim/cli/validate/`) reads DUT state by running the device's
`netlab_show_command` over the node's connection. For a clab/docker node that connection is
`docker exec`; the command template's `$@` is replaced with the string the plugin's `show_*`
function returns. confd's `| display json` emits the OpenConfig subtree as JSON, which the framework
parses into the `_result` global.

1. **`devices/arcos.yml` → `clab.group_vars.netlab_show_command`** (the committed value):
   ```yaml
   netlab_show_command: [ bash, -c, 'f=\$(mktemp); for i in \$(seq 1 60); do
     echo ''show $@ | display json'' | confd_cli -C -u admin > \$f 2>/dev/null;
     python3 -c ''import sys,json; json.load(open(sys.argv[1]))'' \$f 2>/dev/null &&
     { cat \$f; rm -f \$f; exit 0; }; sleep 1; done; cat \$f; rm -f \$f' ]
   ```
   A check that returns the path `network-instance default` becomes, on the box:
   `echo 'show network-instance default | display json' | confd_cli -C -u admin`.
   Two device-specific gotchas are handled inline (see below), which is why the command is a small
   retry loop rather than a one-liner.

2. **`netsim/validate/arcos.py`** (top-level device plugin the resolver loads via
   `package:validate/<device>.py`) aggregates the per-module checks:
   ```python
   from netsim.validate.ospf.arcos import *
   ```

3. **`netsim/validate/ospf/arcos.py`** implements the `ospf_neighbor` check:
   - `show_ospf_neighbor(id,...)` → returns `network-instance <vrf>` (the confd show path).
   - `valid_ospf_neighbor(id,...)` → recursively walks the parsed JSON `_result` for an OSPF
     neighbor whose `neighbor-router-id == id` and `adjacency-state == arcos-ospf-types:NEIGHBOR_FULL`.

This is exactly the SONiC pattern (docker-exec show → device plugin → parse), differing only in the
CLI (`confd_cli … | display json` vs `vtysh -c … json`) and that ArcOS needs an ArcOS-specific
parser (it speaks OpenConfig JSON, not FRR, so it cannot alias `frr` the way `sonic_clab` does).

### Two device-specific gotchas the show command absorbs

Both were caught live; the fixes are self-contained in `netlab_show_command`, so the stock framework
needs no changes:

- **Transient malformed JSON during convergence.** `confd | display json` on the full
  `network-instance default` subtree can briefly emit invalid JSON while OSPF state is churning
  (observed: `Expecting ',' delimiter` at a fixed offset on both nodes, then clean once converged).
  netlab treats a JSON parse failure as *unrecoverable* (no retry) and its `wait:` is a one-time
  up-front delay measured from lab start — which the multi-minute ArcOS boot fully consumes. So the
  show command **retries confd itself** (up to ~60×, validating with `python3 -c json.load`) until
  the output parses, which also lets the adjacency reach FULL (r1 above took 40s of retries).
- **The validate path double-wraps the command in `bash -c`.** `connect.py`'s `quote_list()` wraps
  the script arg in double quotes and `docker_connect()` runs `bash -c "<script>"`, so any `$var` /
  `$(...)` would expand at the *outer* shell (to empty). Every shell `$` in the loop is therefore
  written `\$` so it survives to the inner shell. (An earlier attempt to ship the loop as a file via
  `netlab_start_exec` failed: ArcOS wipes early-boot `/tmp` writes — the same reason its own
  bootstrap needs a 60× retry.)

## Files (branch `device/arcos`)

- `netsim/devices/arcos.yml` — added `netlab_show_command` (inline confd_cli retry) under `clab.group_vars`.
- `netsim/validate/arcos.py` — top-level device plugin (aggregator).
- `netsim/validate/ospf/arcos.py` — OSPFv2 `ospf_neighbor` check (OpenConfig-JSON parser).
- `tests/integration/platform/arcos/02-ospf.yml` — 2-node OSPFv2 test with a `validate:` section.

## Consequence for upstreaming

ArcOS does **not** need the validation-transport PR — the docker-exec + confd_cli path gives it stock
`--validate`, same as SONiC-clab. That framework work is therefore **OcNOS-only** (OcNOS is a vrnetlab
VM whose restricted `cmlsh` can't be reached this way). Remaining ArcOS work to reach parity with
SONiC's coverage is per-module plugins (bgp/isis/route/…) following the ospf one — mechanical, no
framework changes.
