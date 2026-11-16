#!/usr/bin/env bash
# Switch Envoy's load-balancing phase without restarting it (decision A3-9).
#
#   scripts/lb_policy.sh a-only | round-robin | weighted | least-request
#   scripts/lb_policy.sh            # show the active phase and per-host state
#
# Copies monitoring/envoy/clusters/<phase>.yaml to a temporary file in
# monitoring/envoy/active/ and renames it over cds.yaml (an atomic move, which is
# what Envoy's watched_directory reacts to). Envoy reloads the clusters within
# about a second. Then prints, per cluster and host, the weight, health flags and
# active requests from Envoy's admin API. round-robin is the committed (normal) state.
set -euo pipefail
cd "$(dirname "$0")/.."
ADMIN=${ENVOY_ADMIN:-http://localhost:9901}
ACTIVE=monitoring/envoy/active/cds.yaml

if [[ $# -ge 1 ]]; then
  src="monitoring/envoy/clusters/$1.yaml"
  [[ -f "$src" ]] || { echo "unknown phase '$1'; one of: $(ls monitoring/envoy/clusters | sed 's/\.yaml$//' | tr '\n' ' ')" >&2; exit 2; }
  cp "$src" monitoring/envoy/active/.cds.yaml.tmp
  mv monitoring/envoy/active/.cds.yaml.tmp "$ACTIVE"
  echo "$(date -u +%Y-%m-%dT%H:%M:%S.%3NZ) phase -> $1"
  sleep 3  # reload + first health checks (interval 2 s)
fi

head -1 "$ACTIVE"
curl -s "$ADMIN/clusters" | awk -F'::' '
  $1 ~ /^app_(http|grpc)$/ && ($3 == "hostname" || $3 == "weight" || $3 == "health_flags" || $3 == "rq_active" || $3 == "rq_total") {
    key = $1 " " $2; if (!(key in seen)) { seen[key] = 1; order[++n] = key }
    v[key, $3] = $4
  }
  END {
    printf "%-9s %-16s %-4s %-6s %-14s %-9s %s\n", "cluster", "address", "host", "weight", "health", "rq_active", "rq_total"
    for (i = 1; i <= n; i++) { k = order[i]; split(k, a, " ")
      printf "%-9s %-16s %-4s %-6s %-14s %-9s %s\n", a[1], a[2], v[k,"hostname"], v[k,"weight"], v[k,"health_flags"], v[k,"rq_active"], v[k,"rq_total"] }
  }'
