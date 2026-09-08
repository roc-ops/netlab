#!/bin/bash
#
# Tier 1.5 runner: render every platform integration topology offline (issue #87).
#
#   ./render-check.sh
#
# This is the CI check the README documents under "Tier 1.5 -- render every config offline."
# For every topology tracked under tests/integration/platform/*/*.yml it runs
#
#   netlab create <topology> && netlab initial -o config --clean
#
# with no containers, images, or device, and asserts that specific per-module config
# artifacts a topology is known to produce actually got written -- not just that the
# commands exited 0, since a template that silently renders nothing exits 0 too.
#
# See the README for the full rationale (why this and not `create` alone, why the loop is
# driven by `git ls-files` and not a glob, why it must run from each topology's own
# directory). This file only comments on choices the README does not already cover.
#
set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"    # tests/integration/platform
ROOT="$(cd "$HERE/../../.." && pwd)"                     # repo root

# The `netlab` wrapper script at the repo root, not an installed console-script entry point --
# same pattern tests/check-integration-tests.sh already uses (`../netlab`), so this needs
# nothing installed beyond requirements.txt.
NETLAB="$ROOT/netlab"
[ -x "$NETLAB" ] || { echo "cannot find the netlab wrapper script at $NETLAB" >&2; exit 2; }

# Required per-module artifacts, keyed by topology path (relative to the repo root) -> a path
# (relative to the topology's own directory) that `netlab initial -o config` must produce.
#
# This table is deliberately a short, explicit list and not a general rule -- there is no
# general rule to state here. "The command exited 0" proves nothing about a specific module
# having fired, because a template that silently renders nothing also exits 0. The only
# trustworthy check is "this named topology is known, BY HAND, to produce this named file" --
# exactly the two worked examples in the README, confirmed against a live render before being
# added here. To add a row: run the topology through this script's two commands yourself,
# find the artifact under its config/ directory, and only then add the pair below.
declare -A REQUIRED_ARTIFACTS=(
  [tests/integration/platform/sonic/27-tunnel-gre.yml]="config/s1.tunnel.gre.sh"
  [tests/integration/platform/sonic/28-files-maxprefix.yml]="config/s1.bgp-maxprefix.sh"
)

cd "$ROOT" || exit 2

total=0
fail=0
for f in $(git ls-files ':(glob)tests/integration/platform/*/*.yml'); do
  total=$((total + 1))
  d=$(dirname "$f")
  b=$(basename "$f")

  # Snapshot untracked paths in the topology's own directory (and only that directory: the
  # `-- .` pathspec) before touching anything, so cleanup can remove exactly what THIS run
  # produced. ocnos/ and arcos/ are, right now, live tier-2 runs in progress with their own
  # transient untracked files (netlab.lock, clab-<id>/ container state) -- a blind `git
  # clean` would be destructive there, so cleanup is a before/after diff, never a clean.
  before=$(cd "$d" && git status --porcelain -- . | awk '$1=="??"{print $2}' | sort)

  create_log=$(mktemp)
  init_log=$(mktemp)
  create_ok=1
  render_ok=1
  if ! ( cd "$d" && "$NETLAB" create "$b" ) >"$create_log" 2>&1; then
    create_ok=0
  elif ! ( cd "$d" && "$NETLAB" initial -o config --clean ) >"$init_log" 2>&1; then
    render_ok=0
  fi

  artifact_ok=1
  missing=""
  if [ "$create_ok" -eq 1 ] && [ "$render_ok" -eq 1 ] && [ -n "${REQUIRED_ARTIFACTS[$f]+set}" ]; then
    for artifact in ${REQUIRED_ARTIFACTS[$f]}; do
      [ -e "$d/$artifact" ] || { artifact_ok=0; missing="$missing $artifact"; }
    done
  fi

  # Clean up: remove only paths that are newly untracked since `before`. Never remove
  # netlab.lock or a clab-<id>/ directory even if one shows up in the diff -- neither is ever
  # written by `create` or `initial` (only `netlab up`/`down` touch netlab.lock; only a real
  # container runtime creates clab-<id>/), so either one appearing here belongs to a
  # concurrent `netlab up` in the same directory, not to us, and deleting it would be the
  # exact kind of interference we were told not to cause.
  after=$(cd "$d" && git status --porcelain -- . | awk '$1=="??"{print $2}' | sort)
  new=$(comm -13 <(printf '%s\n' "$before") <(printf '%s\n' "$after"))
  while IFS= read -r p; do
    [ -z "$p" ] && continue
    case "$(basename "$p")" in
      netlab.lock) continue ;;
      clab-*) continue ;;
    esac
    rm -rf -- "${ROOT:?}/${p:?}"
  done <<<"$new"

  if [ "$create_ok" -eq 0 ]; then
    echo "=== $f  CREATE FAILED"
    sed 's/^/    /' "$create_log"
    fail=$((fail + 1))
  elif [ "$render_ok" -eq 0 ]; then
    echo "=== $f  RENDER FAILED"
    sed 's/^/    /' "$init_log"
    fail=$((fail + 1))
  elif [ "$artifact_ok" -eq 0 ]; then
    echo "=== $f  ARTIFACT MISSING:$missing"
    fail=$((fail + 1))
  else
    echo "=== $f  OK"
  fi
  rm -f "$create_log" "$init_log"
done

echo
echo "rendered $total topologies, $((total - fail)) ok, $fail failed"
[ "$fail" -eq 0 ]
