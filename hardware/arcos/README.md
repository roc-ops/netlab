# ArcOS hardware bridge + guardrail (issue #39, phase 1 — dry-run only)

Applies the **Casa plan→render→guardrail** pattern to the REAL Edgecore **AS7326-56X**
`roc-leaf2` (ArcOS **S8.5.1A** @ `10.22.64.223`). netlab *plans* the topology; our bridge
*renders* the in-tree `arcos` device templates against that plan; the guardrail *fences* the
management/reachability regions; and — in this phase — the transport would push over SSH→`cli`
but **is hard-blocked from doing so**. The human gate reviews the dry-run before anything is
applied.

> ⛔ **SAFETY.** Phase 1 is read-only SSH + offline render/diff. Nothing here pushes/commits/
> applies config to the hardware. `arcos_transport.apply_push` is hard-disabled and raises.
> The box password is **never** hardcoded or written to any file — it is read from
> `ARCOS_HW_PASSWORD` at run time only. The live running-config is captured to `/tmp` (never
> the repo) with the AAA `$6$…` hash redacted.

## Why hardware reuses the container `arcos` device unchanged

The CLI grammar is **identical** on the container and the hardware — both are `confd_cli`. The
container device reaches it over Ansible's `docker` connection (`docker exec … confd_cli`); the
hardware reaches the *same* `confd_cli` over SSH (`ssh root@host 'echo "…" | cli'`, `cli` =
`confd_cli -R`). **Only the transport differs**, so the render templates
(`netsim/ansible/templates/<module>/arcos.j2`) are reused verbatim.

## Files

| file | role |
|------|------|
| `arcos_guardrail.py` | Management + reachability guardrail. Mgmt lifeline (`system ssh-server`, `system aaa`, `network-instance management`, `interface ma1`) is hardcoded; the reachability set (L3-addressed interfaces — here `loopback0`, `swp56`) is **seeded from the live running-config**. Three fail-closed gates: `scan_config`, `capture_baseline`, `baseline_diff`. |
| `arcos_transport.py` | SSH→`confd_cli` transport. Read-only `show*`; `dry_run_push` builds (never runs) the confd_cli sequence; `apply_push` is hard-disabled. |
| `arcos_hw_bridge.py` | Plan→render bridge (the Casa `casa_netlab_bridge.py` analog). Renders `initial` + module templates against the netlab plan node, gated by the guardrail. |
| `dryrun.py` | Ties it together into the reviewable dry-run report: (a) candidate block, (b) offline diff vs live, (c) protected-regions-intact proof, (d) would-push sequence. |
| `arcos-hw-dryrun.yml` | The dry-run topology: `roc-leaf2` as an `unmanaged` arcos node, one stub link pinned to **swp48** (a genuinely bare port; `swp56` is deliberately avoided — it is the live underlay uplink). `loopback: false` so netlab never re-owns loopback0 (the live EVPN router-id). |
| `plan-roc-leaf2.yml` | The netlab-transformed node plan (addressing only, no secrets), extracted from the snapshot for the bridge. |

## Reproduce the dry-run

```bash
cd ~/nl-wt/arcos-hw && source ~/netlab-venv/bin/activate
netlab create hardware/arcos/arcos-hw-dryrun.yml                # plan (no deploy)
# extract plan node -> hardware/arcos/plan-roc-leaf2.yml (see snapshot pickle)
export ARCOS_HW_PASSWORD=…                                      # NEVER commit this
# capture read-only baseline (hash redacted), then the dry-run report:
python3 hardware/arcos/dryrun.py hardware/arcos/plan-roc-leaf2.yml \
        netsim/ansible/templates /tmp/arcos-live.txt roc-leaf2
```

## Guardrail negative test (fail-closed proof)

A plan that lets netlab own `loopback0` is **refused at render time**:

```
$ python3 hardware/arcos/arcos_hw_bridge.py <naive-plan> netsim/ansible/templates \
          roc-leaf2 --running-config /tmp/arcos-live.txt
REFUSED: config opens `interface loopback0` -- protected (reachability-critical, live-addressed)
```

## Template change on this branch

`netsim/ansible/templates/initial/arcos.j2` now emits the `interface loopback0` stanza **only
when the node actually owns a loopback** (`loopback.ipv4`/`ipv6` defined). Previously it emitted
a bare `interface loopback0` unconditionally — harmless in a container, but on real hardware
that would have the render touch the live router-id interface. No-op for container nodes (they
always carry `loopback.ipv4`).
