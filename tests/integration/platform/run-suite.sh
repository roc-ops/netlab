#!/bin/bash
#
# Tier 2 runner for a platform integration suite (issue #87).
#
#   ./run-suite.sh sonic                 # deploy + validate every topology in sonic/
#   ./run-suite.sh sonic 06-mpls-sr-l3vpn.yml 27-tunnel-gre.yml
#
# One lab at a time: these suites pin a multilab id, and two of them at once collide on the
# management network. Set ML_ID to something you have checked against `netlab status --all` --
# do NOT rely on ml_autoid, which reads running containers and its own reservations but not
# netlab's instance registry, and can hand out an id netlab considers taken (issue #92).
#
# LOGS OF FAILING RUNS ARE KEPT. Each run writes to a fixed path for convenience, and any run
# that fails is ALSO copied to a timestamped KEEP- name. That is not tidiness: during #87 a
# single unreproducible 03-bgp failure was rendered undiagnosable because the follow-up runs
# overwrote the only log of it. A flake you cannot read is a flake you cannot diagnose.
#
set -u

SUITE="${1:-}"
if [ -z "$SUITE" ]; then
  echo "usage: $0 <suite-directory> [topology.yml ...]" >&2
  exit 2
fi
shift

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DIR="$HERE/$SUITE"
[ -d "$DIR" ] || { echo "no such suite: $DIR" >&2; exit 2; }

LOGDIR="${LOGDIR:-${TMPDIR:-/tmp}/netlab-suite-$SUITE}"
mkdir -p "$LOGDIR"
ML_ID="${ML_ID:-}"
ID_ARG=()
[ -n "$ML_ID" ] && ID_ARG=(-s "defaults.multilab.id=$ML_ID")

if [ "$#" -gt 0 ]; then
  TOPOS=("$@")
else
  # Tracked files only: netlab writes clab.yml and hosts.yml beside each topology, and a plain
  # glob picks those up on the second run and tries to build them as topologies.
  mapfile -t TOPOS < <(cd "$DIR" && git ls-files ':(glob)*.yml' | sort)
fi

cd "$DIR" || exit 2
pass=0; fail=0; warn=0
for t in "${TOPOS[@]}"; do
  stamp=$(date +%Y%m%d-%H%M%S)
  netlab down --cleanup >/dev/null 2>&1
  if ! netlab up "$t" "${ID_ARG[@]}" > "$LOGDIR/up-$t.log" 2>&1; then
    cp "$LOGDIR/up-$t.log" "$LOGDIR/KEEP-up-$t-$stamp.log"
    echo "=== $t  DEPLOY FAILED   (kept: $LOGDIR/KEEP-up-$t-$stamp.log)"
    fail=$((fail+1))
    continue
  fi
  netlab validate > "$LOGDIR/validate-$t.log" 2>&1
  rc=$?
  case $rc in
    0) echo "=== $t  PASS   $(grep -oE 'Tests passed: [0-9]+' "$LOGDIR/validate-$t.log" | tail -1)"
       pass=$((pass+1)) ;;
    3) cp "$LOGDIR/validate-$t.log" "$LOGDIR/KEEP-validate-$t-$stamp.log"
       echo "=== $t  WARNING   (kept: $LOGDIR/KEEP-validate-$t-$stamp.log)"
       grep -E '^\[WARNING\]' "$LOGDIR/validate-$t.log"
       warn=$((warn+1)) ;;
    *) cp "$LOGDIR/validate-$t.log" "$LOGDIR/KEEP-validate-$t-$stamp.log"
       cp "$LOGDIR/up-$t.log" "$LOGDIR/KEEP-up-$t-$stamp.log"
       echo "=== $t  FAIL   (kept: $LOGDIR/KEEP-validate-$t-$stamp.log)"
       grep -E '^\[FAIL\]' "$LOGDIR/validate-$t.log"
       fail=$((fail+1)) ;;
  esac
done
netlab down --cleanup >/dev/null 2>&1

echo
echo "pass=$pass warn=$warn fail=$fail   logs: $LOGDIR"
[ "$fail" -eq 0 ]
