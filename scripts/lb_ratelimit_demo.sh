#!/usr/bin/env bash
# What goes wrong with per-replica state (A3-4): the rate-limit buckets live in
# each replica's memory, so behind round robin a device gets one bucket per replica.
#
#   scripts/lb_ratelimit_demo.sh | tee docs/results/a3/a_rate_limit_per_replica.txt
#
# Sends a burst of 7 POST /readings for one device through Envoy, first with only
# replica A as a destination, then with round robin over A, B and C, and prints
# each response's status and replica. The limit is a burst of 5, then 1/s per device.
# Leaves Envoy in round-robin (the normal state).
set -euo pipefail
cd "$(dirname "$0")/.."
URL=${URL:-http://localhost:8000}

burst() {  # $1 = phase, $2 = device
  scripts/lb_policy.sh "$1" >/dev/null
  echo "  (Envoy phase: $1)"
  for i in $(seq 1 7); do
    ts=$(date -u +%Y-%m-%dT%H:%M:%S.%3NZ)
    curl -s -o /dev/null -D - -X POST "$URL/readings" -H 'Content-Type: application/json' \
      -H "X-Request-ID: rl-$1-$i" \
      -d "{\"device_id\":\"$2\",\"timestamp\":\"$ts\",\"temperature_c\":22.5,\"humidity_pct\":45,\"battery_pct\":88}" |
      tr -d '\r' | awk -v n="$i" '/^HTTP/{s=$2} tolower($1)=="x-replica:"{r=$2} END{printf "  request %d -> %s from replica %s\n", n, s, r}'
  done
}

echo "== A only: one bucket for dev-801 (expect 5 x 201, then 429)"
burst a-only dev-801
echo "== Round robin: three buckets for dev-802, one per replica (expect 7 x 201)"
burst round-robin dev-802
