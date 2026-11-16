#!/usr/bin/env bash
# Set the fault injection of ONE replica, directly, never through Envoy (A3-7).
#
#   scripts/replica_fault.sh B delay 200     # every test request on B waits 200 ms
#   scripts/replica_fault.sh B error 1       # every test request on B fails (500 / INTERNAL)
#   scripts/replica_fault.sh B off           # back to normal
#   scripts/replica_fault.sh B               # show B's current fault config
#
# Faults apply to POST /readings, GET /devices/{device_id} and gRPC GetDevice on that
# replica (one process, so HTTP and gRPC together). The request runs inside the
# replica's container (docker exec), against its own localhost:8000/admin/faults.
set -euo pipefail
replica=${1:?replica letter, e.g. B}
index=$(( $(printf '%d' "'${replica^^}") - 64 ))   # A=1, B=2, C=3, …
container="esd_hw3-app-$index"
case "${2:-show}" in
  delay) method=PUT;    body="{\"delay_ms\": ${3:?ms}, \"delay_every_n\": 1}" ;;
  error) method=PUT;    body="{\"error_every_n\": ${3:-1}}" ;;
  off)   method=DELETE; body="" ;;
  show)  method=GET;    body="" ;;
  *) echo "usage: $0 <replica> [delay <ms> | error <every_n> | off]" >&2; exit 2 ;;
esac
echo "$(date -u +%Y-%m-%dT%H:%M:%S.%3NZ) $container ($replica): $method ${body:-}"
docker exec "$container" python -c "
import sys, urllib.request
body = sys.argv[2].encode() or None
req = urllib.request.Request('http://localhost:8000/admin/faults', data=body, method=sys.argv[1],
                             headers={'Content-Type': 'application/json', 'X-Request-ID': 'fault-$replica'})
print(urllib.request.urlopen(req, timeout=5).read().decode())
" "$method" "$body"
