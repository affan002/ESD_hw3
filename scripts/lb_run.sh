#!/usr/bin/env bash
# One measured run of the A3 workload, per interface, in a given Envoy phase.
#
#   scripts/lb_run.sh <phase> <run-name> [http|grpc|both]
#   scripts/lb_run.sh a-only b_baseline                # Part B
#   scripts/lb_run.sh round-robin c_round_robin        # Part C
#
# Switches Envoy to <phase> (no restart), waits 10 s, then for each interface:
# scripts/lb_load.py at RATE (50) req/s for DURATION (120) s →
# docs/results/a3/<run-name>_<iface>.json, then scripts/lb_run_stats.py → …_server.json,
# then IDLE (30) s before the next interface, so the two never overlap in a [1m] window.
# Faults are NOT touched here: set them before with scripts/replica_fault.sh.
#   OUT=/tmp/a3-try DURATION=60 scripts/lb_run.sh round-robin try_rr   # a short practice run elsewhere
set -euo pipefail
cd "$(dirname "$0")/.."
phase=${1:?phase: a-only | round-robin | weighted | least-request}
name=${2:?run name, e.g. b_baseline}
which=${3:-both}
RATE=${RATE:-50} DURATION=${DURATION:-120} IDLE=${IDLE:-30}
OUT=${OUT:-docs/results/a3}   # set OUT=/some/dir for practice runs, so the submitted results stay untouched
mkdir -p "$OUT"
PY=${PY:-.venv/bin/python}

scripts/lb_policy.sh "$phase"
sleep 10
ifaces=(http grpc); [[ $which != both ]] && ifaces=("$which")
for iface in "${ifaces[@]}"; do
  run=${name#*_}; run=${run//_/-}   # request ids: lb-<run>-<iface>-NNNNN, e.g. lb-baseline-http-00042
  "$PY" scripts/lb_load.py --interface "$iface" --run "$run" --rate "$RATE" --duration "$DURATION" \
    --out "$OUT/${name}_${iface}.json" || true   # non-zero = some requests failed; still keep the numbers
  python3 scripts/lb_run_stats.py "$OUT/${name}_${iface}.json" > /dev/null
  python3 - "$OUT/${name}_${iface}.json" "$OUT/${name}_${iface}_server.json" <<'PY'
import json, sys
c, s = json.load(open(sys.argv[1])), json.load(open(sys.argv[2]))
print(f"{c['run']}/{c['interface']}: {c['actual_rps']} rps, sent {c['sent']}, ok {c['successes']}, failed {c['failures']}, "
      f"client p95 {c['client_latency_ms'].get('p95')} ms | client shares {c['replica_shares_pct']} | "
      f"server shares { {r: v['share_pct'] for r, v in s['replicas'].items()} } | "
      f"Envoy { {h: v.get('rq_total') for h, v in s['envoy_endpoints'].items()} }")
PY
  [[ $iface != "${ifaces[-1]}" ]] && sleep "$IDLE"
done
