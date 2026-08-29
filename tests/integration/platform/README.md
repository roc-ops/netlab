# Platform integration suites

Per-device bring-up suites. These are the evidence base we cite when we say a device supports
something, so what they claim has to stay true.

| suite | device | topologies | last full live re-run |
|---|---|---|---|
| [`sonic/`](sonic/) | `sonic` (docker-sonic-vs, clab) | 29 | 2026-08-29, all 29 |
| [`arcos/`](arcos/) | `arcos` (arcos:8.2.1A.P2, clab) | 1 | 2026-08-29 |
| [`ocnos/`](ocnos/) | `ocnos` (vrnetlab/ipinfusion_ocnos, clab) | 1 | 2026-08-29 |

## Three claims, never one

Every row in every suite states three separate things, and none of them may stand in for
another:

1. **CREATES** -- `netlab create` succeeds. Seconds. No containers, no device, no image.
2. **DEPLOYS** -- `netlab up` completes and the configuration actually applies on the device.
3. **RESULT** -- the thing the row claims (a datapath, a protocol state) actually holds, read
   off the running lab.

A row that creates is not a row that passes. Write the three separately, and when only half of
a claim holds, say which half -- do not round it up to a pass or quietly delete it.

## Why this file exists

Issue #87. A `sonic` row recorded as "passed live, 0% loss" could no longer even be **created**:
a device feature flag had changed shape underneath it (`gre: true` where the plugin tests list
membership) and nothing re-ran the test. It was found by accident, while migrating the device
for something else.

Re-running everything then found more of the same, and it is worth being precise about what
each tier would and would not have caught, because that is the whole argument for having more
than one tier:

| what was broken | tier 1 (create) | tier 1.5 (render) | tier 2 (deploy) |
|---|---|---|---|
| `27-tunnel-gre`: feature flag declared as a bool | **caught** | caught | caught |
| `vrrp.version`: template missing from the search path | sometimes | **caught** | caught |
| `sonic-clab.j2`: IPv6 branch missing a guard, valid bash the device rejects | no | no | **caught** |
| `16-ebgp-multihop`: statics hardcoded to a pool that moved | no | no | **caught** |
| `21-evpn-multihoming`: workaround hardcoded to a pool that moved | no | no | **caught** |
| `20-ripv2`: `router ripng` configured, `ripngd` never started | no | no | **caught** |

Half of those are invisible to anything that does not run the lab. A cheap check that runs
often is worth having and is not a substitute for running the thing.

## The tiers

### Tier 1 -- `netlab create`, every topology. CI-able today.

No containers, no images, no device. About a second per topology; the full 31 finish in well
under a minute. This is the tier to automate.

```bash
# from a checkout of the fork
fail=0
for f in tests/integration/platform/*/*.yml; do
  ( cd "$(dirname "$f")" && netlab create "$(basename "$f")" >/dev/null ) \
    || { echo "CREATE FAILED: $f"; fail=1; }
done
exit $fail
```

Run it from each topology's own directory: some topologies reference sibling paths
(`sonic/h1-fixaddr/`) that only resolve relative to the file. Clean the generated `clab.yml`,
`hosts.yml`, `ansible.cfg`, `group_vars/`, `host_vars/`, `node_files/` and
`netlab.snapshot.*` afterwards, or run against a copy of the tree.

### Tier 1.5 -- render every config offline. Also CI-able, same cost, strictly stronger.

```bash
netlab create "$topology" && netlab initial -o config --clean
```

`netlab initial -o` renders each node's initial, per-module and per-plugin configuration to
files **without touching a device**. It exercises the Jinja search path, so it catches a
template that has been deleted, renamed, or never existed for this device -- the class of
failure that produced "Cannot find vrrp.version configuration template". Assert that the
expected per-module scripts exist, not merely that the command exited 0: `27-tunnel-gre` should
produce `s1.tunnel.gre.sh`, `28-files-maxprefix` should produce `s1.bgp-maxprefix.sh`.

Known limitation, stated plainly: this tier renders. It cannot tell you the device will accept
what was rendered. Every one of #86's eight commits was verified at render time by two people
and the deploy still found a bug none of that could have.

### Tier 2 -- deploy and check the claim. Not CI-able; scheduled and human-driven.

Needs real images (`docker-sonic-vs`, and commercial images for ArcOS and OcNOS), real
containers, and minutes per topology. The full `sonic` suite is roughly an hour of wall clock
one lab at a time. This is the only tier where "0% loss" claims actually live.

Prioritise **datapath** rows over protocol-state rows: they are the strongest claims and the
ones most likely to have rotted.

## Cadence and triggers

**Tier 1 + 1.5: on every push, in CI.** They need nothing but the repo.

**Tier 2, whenever any of these happen -- these are triggers, not a schedule:**

* **Before merging any change to a device file's feature flags** (`netsim/devices/*.yml`).
  Non-negotiable: this is exactly what broke #87. A feature flag is a claim about a device, and
  the suite is the evidence for it. Changing the claim without re-running the evidence is how
  a recorded pass becomes fiction.
* **Before merging any change to a device's templates** (`netsim/ansible/templates/**/<device>*.j2`,
  and the plugin search paths under `netsim/extra/`). Re-run at least the rows whose `module(s)`
  column names the touched module.
* **After a device convergence, rename or retirement** (as in #86) -- the whole suite for that
  device, not a sample. #86 sampled six rows of 29 and three were wrong.
* **After bumping a device image.**
* **On a quarterly floor**, for any suite no trigger has fired on. Rot here is measured in
  months: the pool-drift bugs in `16` and `21` had been unreproducible for an unknown but long
  time and nobody noticed, because nothing ran.

**Whoever re-runs, records the date and what was measured in the row itself.** Not "verified" --
the number. "IPv4 5/5, IPv6 4/4, 0% loss" survives review; "passes" does not.

## Rules that came out of #87

* **Never hardcode a pool-allocated address in a topology, a workaround, or a comment.** Use the
  symbolic forms (`node:`, `nexthop.node:`, `vlans.<name>.prefix.*`). netlab's default pools
  moved from 10.0.0.0/24 + 10.1.0.0/24 to 10.8.0.0/24 + 10.10.0.0/30, and from 172.16.0.0 to
  172.18.0.0, and every literal written against the old pools silently stopped meaning what it
  said. `24-routing-v6` uses the symbolic form and did not rot; `16-ebgp-multihop` used literals
  and did.
* **A topology that builds both address families needs both checked.** `16` created an IPv6
  multihop session it never gave IPv6 reachability to. `20-ripv2` renders `router ripng` that
  never runs. `ocnos/ospf-bgp` had a working IPv6 BGP session nobody had ever looked at. A
  single-AF check on a dual-stack lab is half a test -- record it as half, or check both.
* **Establish absence before reporting it.** If a check returns nothing, find out whether the
  thing is missing or the query was wrong. Several "failures" during #87's re-run were the
  wrong ping target or the wrong `show` command, not the lab.
* **Read convergence state at the same moment as the result.** An unconverged overlay produces
  a false FAILURE as easily as a stale README produces a false pass.
* **Pin the multilab id you have checked.** `ml_autoid` reads running containers and its own
  reservations but not netlab's instance registry; during #87 it handed out an id netlab
  considered taken by another lab's directory. It failed safe, but check `netlab status --all`.
* **A declaration is not an effect.** `defaults.multilab.id` without `plugin: [ multilab ]` does
  nothing (#86) or errors outright (#87). `router ripng` without `ripngd=yes` does nothing.
  Verify the effect, never the declaration.

## Not done, and deliberately named

**None of these topologies has a `validate:` block.** Every claim in every suite exists only as
English prose in a README, which is precisely why they can rot without anything failing.
Machine-checkable `validate:` blocks would turn tier 2 from a human reading `show` output into
something a scheduled job can run and fail. That is the real fix and it is not done here.
