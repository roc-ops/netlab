# Platform integration suites

Per-device bring-up suites. These are the evidence base we cite when we say a device supports
something, so what they claim has to stay true.

| suite | device | topologies | machine-checkable | last full live re-run |
|---|---|---|---|---|
| [`sonic/`](sonic/) | `sonic` (docker-sonic-vs, clab) | 29 | yes -- every row has a `validate:` block | 2026-08-29, all 29 |
| [`arcos/`](arcos/) | `arcos` (arcos:8.2.1A.P2, clab) | 1 | not yet | 2026-08-29 |
| [`ocnos/`](ocnos/) | `ocnos` (vrnetlab/ipinfusion_ocnos, clab) | 1 | not yet | 2026-08-29 |

Both the ArcOS and the OcNOS images are ones we build and hold ourselves -- the upstream package
does not ship them. They are the exact tags the device defaults name, so both suites are in
scope for a real deploy, not excused for want of an image.

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

The root cause was not the flag. It was that **nothing could fail**. Every claim in the suite
existed only as English prose in a README, so no amount of rot produced a red result anywhere.
That is now fixed for `sonic`: every row carries a `validate:` block.

Re-running everything found more of the same, and it is worth being precise about which tier
would have caught which bug, because that is the whole argument for having more than one:

| what was broken | tier 1 (create) | **tier 1.5 (render)** | tier 2 (deploy + validate) |
|---|---|---|---|
| `27-tunnel-gre`: feature flag declared as a bool | caught | **caught** | caught |
| `vrrp.version`: template missing from the search path | sometimes | **caught** | caught |
| `sonic-clab.j2`: IPv6 branch missing a guard, valid bash the device rejects | no | no | **caught** |
| `16-ebgp-multihop`: statics hardcoded to a pool that moved | no | no | **caught** |
| `21-evpn-multihoming`: workaround hardcoded to a pool that moved | no | no | **caught** |
| `20-ripv2`: `router ripng` configured, `ripngd` never started | no | no | **caught** |

**Four of those six are invisible to anything that does not run the lab** -- the two template
bugs and both pool-drift bugs. A cheap check that runs often is worth having and is not a
substitute for running the thing.

## The tiers

### Tier 1.5 -- render every config offline. THIS IS THE CI CHECK.

```bash
netlab create "$topology" && netlab initial -o config --clean
```

No containers, no images, no device -- the same cost as a bare `netlab create` sweep, and
strictly stronger, which is why this and not `create` is what belongs in CI. `netlab initial -o`
renders each node's initial, per-module and per-plugin configuration to files **without touching
a device**. It therefore exercises the Jinja search path, catching a template that has been
deleted, renamed, or never existed for this device -- the class of failure that produced
"Cannot find vrrp.version configuration template", and exactly the class a device convergence
(#86 renamed templates wholesale) is most likely to introduce.

`netlab create` still runs first, because rendering depends on it; a create failure is reported
as one. The point is that stopping at create leaves free coverage on the table.

Assert that the expected per-module scripts **exist**, not merely that the command exited 0:
`27-tunnel-gre` must produce `s1.tunnel.gre.sh`, `28-files-maxprefix` must produce
`s1.bgp-maxprefix.sh`. A template that silently renders nothing exits 0.

```bash
# Runs from a checkout of the fork. Tested verbatim: 31/31 topologies, well under a minute,
# and run three times in a row in the same tree to prove the property below.
fail=0
for f in $(git ls-files ':(glob)tests/integration/platform/*/*.yml'); do
  d=$(dirname "$f"); b=$(basename "$f")
  ( cd "$d" && netlab create "$b" >/dev/null && netlab initial -o config --clean >/dev/null ) \
    || { echo "RENDER FAILED: $f"; fail=1; }
done
exit $fail
```

**The loop is driven by `git ls-files`, and that is load-bearing, not a style choice.**
`netlab create` writes `clab.yml` and `hosts.yml` *beside* each topology, and those match a
naive `tests/integration/platform/*/*.yml` glob. An earlier version of this snippet used that
glob: it exits 0 the first time and fails with **exactly six** `RENDER FAILED` lines the second
time, in the same tree, having tried to build `clab.yml` and `hosts.yml` as if they were
topologies. On a check whose entire selling point is being cheap enough to run on every push,
that is six false failures on every run after the first. `git ls-files` cannot match a generated
file, because a generated file is untracked -- which makes idempotency a structural property of
the script rather than something a cleanup step has to keep true.

Run it from each topology's own directory: some topologies reference sibling paths
(`sonic/h1-fixaddr/`) that only resolve relative to the file. The generated `clab.yml`,
`hosts.yml`, `ansible.cfg`, `config/`, `group_vars/`, `host_vars/`, `node_files/` and
`netlab.snapshot.*` are still worth cleaning up so the working tree stays clean -- but with
`git ls-files` driving the loop, forgetting to no longer breaks the next run.

**The honest limit, stated plainly:** this tier renders. It cannot tell you the device will
accept what was rendered. Every one of #86's eight commits was verified at render time by two
people and the deploy still found a bug none of that could have -- a template emitting valid
bash that the device rejects. Tier 1.5 is a first tier, not a substitute for running the lab.

### Tier 2 -- deploy and validate. Not CI-able; triggered and human-driven.

```bash
netlab up <topology> && netlab validate
```

Needs real images, real containers, and minutes per topology. The full `sonic` suite is roughly
an hour of wall clock one lab at a time. This is the only tier where "0% loss" claims live.

Every `sonic` topology now has a `validate:` block, so this tier is a pass/fail run rather than
a human reading `show` output. Prioritise **datapath** rows over protocol-state rows if you
cannot run everything: they are the strongest claims and the ones most likely to have rotted.

## Writing `validate:` blocks

The `sonic` suite's blocks are the worked example. Rules that came out of writing them, several
learned by getting them wrong first:

* **Only encode what you measured.** Writing 29 blocks from the README's prose would have
  replaced 29 unverified prose claims with 29 unverified machine claims -- the same defect
  wearing a different hat. Every block in `sonic/` was written after the result was confirmed
  by hand on a running lab.
* **Never hardcode an address.** Derive it: `{{ hostvars.s2.loopback.ipv4|ipaddr('address') }}`,
  `{{ vlans.red.prefix.ipv4|ipaddr(200) }}` (offset 200 is provably clear of netlab's own
  id-based allocation, not merely clear today: `defaults/const.yml` sets `MAX_NODE_ID: 150`,
  enforced as `max_value` in `augment/nodes.py`, so netlab cannot assign a node id above 150 --
  raising that constant is the one change that would invalidate the offset), `{{ hostvars.s1.interfaces|selectattr('vrf','equalto','red')|map(attribute='ipv4')|first|ipaddr('address') }}`.
  Other nodes are reachable as `hostvars.<name>`, not `nodes.<name>`; the executing node's own
  data is at the top level.
* **A negative assertion needs a positive half.** `'vrf blue' not in stdout` is satisfied by a
  command that returned nothing at all, so a broken device passes. Pair it:
  `'IPv4 unicast VRF red' in stdout and 'vrf blue' not in stdout`.
* **Absence of a string is not absence of the thing.** `blue_no_route` originally ran
  `show ip route <host-address>` and looked for `% Network not in table`. FRR prints that only
  for a *prefix* lookup and returns empty output for a host address, so the test failed while
  the topology was behaving perfectly. It now asserts an unreachable datapath instead.
* **Validation captures stdout only.** `ping`'s "Network is unreachable" goes to stderr. Without
  `2>&1` a correct negative result reads as a failure.
* **Wait for the thing to EXIST before judging its state.** A check that asserts a known defect
  (an ES that stays down) reads too early, `show evpn es detail` prints nothing, which is
  neither "up" nor "down" -- and the test reports that the defect has been fixed. An
  unconverged overlay produces a false result in both directions, and the direction that costs
  you most is the one that says a bug is gone.
* **A test can encode an ACCIDENT of one run and look stable until the accident changes.** This
  is the subtlest of the five, because nothing about it looks like a mistake: the check is
  precise, it measures a real thing, it passes repeatedly -- and the precision is exactly what
  makes it wrong. `21-evpn-multihoming`'s known-defect check originally asserted that **s2's**
  Ethernet Segment stays down. Measured three consecutive deploys in one session: s1 up, s2
  down, every time. Measured three consecutive deploys in a later session: **s2 up, s1 down**,
  every time. The PortChannel MACs are generated per deploy, so nothing prefers either switch
  -- which one wins is a coin flip that happens to be stable within a session. The check passed
  for hours and then announced that issue #93 had been fixed, when all that had changed was the
  coin. The fix is to measure the defect where the answer cannot flip: on the HOST, where
  exactly one port ends up in the active LACP aggregator, whichever switch it belongs to. Ask
  of every assertion: *if the lab were rebuilt, could this still be true and yet name the wrong
  thing?*
* **Pin known defects as tests.** `10-vrrp-version` and `21-evpn-multihoming` assert the broken
  state at `level: warning`, so the day someone fixes it the suite says so instead of quietly
  carrying a stale caveat forever.
* **Test names are limited to 16 characters.** `datapath_single_leg` fails at create time.
* **Keep the log of a run that failed.** `run-suite.sh` writes each failing run's deploy and
  validate logs to a timestamped `KEEP-` copy, because the fixed-path version let a re-run
  overwrite the one failing log that mattered -- see the `03-bgp` note in `sonic/03-bgp.yml`
  for what that cost. A flake you cannot read is a flake you cannot diagnose.

## Cadence and triggers

**Tier 1.5: on every push, in CI.** It needs nothing but the repo.

**Tier 2, whenever any of these happen -- these are triggers, not a schedule:**

* **Before merging any change to a device file's feature flags** (`netsim/devices/*.yml`).
  Non-negotiable: this is exactly what broke #87. A feature flag is a claim about a device, and
  the suite is the evidence for it. Changing the claim without re-running the evidence is how
  a recorded pass becomes fiction. The `netlab_frr_daemons` map in the same file is the same
  kind of claim -- a missing entry there is what made `20-ripv2`'s IPv6 half inert.
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

* **Never hardcode a pool-allocated address in a topology, a workaround, or a comment.** This is
  a *class*, not three incidents: a test asset that hardcodes an address the framework allocates
  is a rot generator, and #87 found three instances (`16-ebgp-multihop`'s statics,
  `21-evpn-multihoming`'s `h1-fixaddr` workaround, `06`'s header comment). netlab's defaults
  moved from 10.0.0.0/24 + 10.1.0.0/24 to 10.8.0.0/24 + 10.10.0.0/30, and from 172.16.0.0 to
  172.18.0.0, and every literal written against the old pools silently stopped meaning what it
  said. Use the symbolic forms (`node:`, `nexthop.node:`, `vlans.<name>.prefix.*`).
  `24-routing-v6` uses the symbolic form and did not rot; `16-ebgp-multihop` used literals and
  did.
* **A topology that builds both address families needs both checked.** `16` created an IPv6
  multihop session it never gave IPv6 reachability to -- so the v6 half could never have worked
  *even on the day it was recorded as passing*, which is a stronger statement than "it broke".
  `20-ripv2` rendered `router ripng` that never ran. `ocnos/ospf-bgp` had a working IPv6 BGP
  session nobody had ever looked at. A single-AF check on a dual-stack lab is half a test.
* **Preconditions belong inside the artifact, not in a human's head.** `06-mpls-sr-l3vpn`
  carried a "one-time host prereq: `sudo modprobe mpls_router mpls_iptunnel`" note. netlab has a
  `kmods:` mechanism for exactly this and it already covers `mpls`/`sr`; `netlab up` prints
  `Loading Linux kernel modules mpls-router,mpls-iptunnel` and loads them itself. The note had
  become a stale instruction to do by hand what the tool does. A result whose preconditions live
  outside the artifact is not reproducible; check for a mechanism before documenting a manual
  step.
* **Establish absence before reporting it.** If a check returns nothing, find out whether the
  thing is missing or the query was wrong. This bit repeatedly during #87 -- a `show` command
  that only prints its "not found" message for one argument form, a ping error on stderr, an
  object read before it existed. Every one of those looked exactly like a real failure.
* **Read convergence state at the same moment as the result.** An unconverged overlay produces
  a false FAILURE as easily as a stale README produces a false pass.
* **Pin the multilab id you have checked.** `ml_autoid` reads running containers and its own
  reservations but not netlab's instance registry, so it can hand out an id netlab considers
  taken (issue #92). Check `netlab status --all`.
* **A declaration is not an effect.** `defaults.multilab.id` without `plugin: [ multilab ]` does
  nothing (#86) or errors outright (#87). `router ripng` without `ripngd=yes` does nothing.
  Verify the effect, never the declaration.

## Still to do

* **`validate:` blocks for `arcos/` and `ocnos/`.** Both suites were deploy-verified by hand on
  2026-08-29 and their READMEs record what was measured, but neither is machine-checkable yet.
  ArcOS needs its OpenConfig state paths (`show network-instance default protocol`) rather than
  FRR-style `show` output; OcNOS needs the `ansible:` validation transport, because its `cmlsh`
  restricted shell has no non-interactive exec mode and always exits 1 -- a non-zero exit there
  is not evidence of failure.
* **Wiring these suites into netlab's shared per-module `tests/integration/<module>/` matrix**,
  so a device is exercised by the same tests as every other device rather than only by its own.
