#!/usr/bin/env bash
# Capture the A3 report screenshots into docs/screenshots/a3/ with headless Chrome
# (same approach as scripts/capture_screenshots.sh: fixed absolute windows, light theme).
#
#   scripts/capture_a3_screenshots.sh grafana    # dashboard panels for every run in docs/results/a3/
#   scripts/capture_a3_screenshots.sh kibana     # Kibana searches (logs live ~70 min: run soon after D3)
#   scripts/capture_a3_screenshots.sh all
#   ONLY=d_failure scripts/capture_a3_screenshots.sh grafana   # one run's panels only
#
# Grafana windows come from each run's client summary: 30 s before its start to 60 s after
# its end, so the [1m] panels cover the whole run. Prometheus keeps 7 days, so these can be
# re-captured any time; Kibana ones only while the run's logs still exist.
set -euo pipefail
cd "$(dirname "$0")/.."
WHAT=${1:-all}
OUT=docs/screenshots/a3
R=docs/results/a3
PROFILE="$(mktemp -d)"
trap 'rm -rf "$PROFILE"' EXIT
mkdir -p "$OUT"

shot() {  # url file width,height [budget ms]
  local before=0
  [[ -f "$OUT/$2" ]] && before=$(stat -c %s "$OUT/$2")
  timeout 120 google-chrome --headless=new --disable-gpu --hide-scrollbars --no-first-run \
    --no-default-browser-check --user-data-dir="$PROFILE" --window-size="$3" \
    --virtual-time-budget="${4:-15000}" --screenshot="$OUT/$2" "$1" >/dev/null 2>&1
  local after; after=$(stat -c %s "$OUT/$2")
  echo "  $OUT/$2 ($after bytes$([[ $after == "$before" ]] && echo ', UNCHANGED: capture probably failed'))"
}

window() {  # run-json pad_before_s pad_after_s -> "from_ms to_ms"
  python3 - "$1" "$2" "$3" <<'PY'
import json, sys
from datetime import datetime
c = json.load(open(sys.argv[1]))
ts = lambda s: datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
print(int((ts(c["start_utc"]) - float(sys.argv[2])) * 1000), int((ts(c["end_utc"]) + float(sys.argv[3])) * 1000))
PY
}

panels() {  # run-name iface "panelId:suffix ..."
  [[ -n ${ONLY:-} && $1 != "$ONLY" ]] && return
  local json="$R/$1_$2.json"
  [[ -f $json ]] || { echo "  (no $json)"; return; }
  read -r from to < <(window "$json" 30 60)
  for p in $3; do
    shot "http://localhost:3000/d-solo/lb-replicas?orgId=1&theme=light&from=$from&to=$to&panelId=${p%%:*}" \
      "$1_$2_${p#*:}.png" 1000,360
  done
}

if [[ $WHAT == grafana || $WHAT == all ]]; then
  echo "Grafana panels (dashboard lb-replicas)"
  # HTTP panels: 6 rate, 7 share, 8 active, 9 p50/p95, 10 errors; gRPC: 12, 13, 14, 15, 16;
  # Envoy: 18 requests per endpoint, 19 healthy endpoints, 20 health-check/connect failures.
  for run in b_baseline c_round_robin d_weighted d_delay_rr d_delay_lr; do
    panels "$run" http "6:rate 7:share 8:active 9:latency"
    panels "$run" grpc "12:rate 13:share 14:active 15:latency"
  done
  panels d_failure http "6:rate 10:errors 19:healthy 20:hc_failures"
  panels d_failure grpc "12:rate 16:errors 19:healthy 20:hc_failures"
  # The targets page is live (not history), so it is only re-captured on request, with
  # the whole stack running: TARGETS=1 scripts/capture_a3_screenshots.sh grafana
  if [[ ${TARGETS:-0} == 1 ]]; then shot "http://localhost:9090/targets" b_prometheus_targets.png 1400,900; fi
fi

if [[ $WHAT == kibana || $WHAT == all ]]; then
  echo "Kibana searches"
  kib() {  # run-json kql columns file
    read -r from to < <(window "$1" 5 10)
    local f t; f=$(date -u -d "@$((from / 1000))" +%Y-%m-%dT%H:%M:%S.000Z); t=$(date -u -d "@$((to / 1000))" +%Y-%m-%dT%H:%M:%S.000Z)
    local q=${2//\"/%22}; q=${q// /%20}
    shot "http://localhost:5601/app/discover#/?_g=(time:(from:'$f',to:'$t'))&_a=(columns:!($3),dataSource:(dataViewId:sensor-logs,type:dataView),query:(language:kuery,query:'$q'),sort:!(!('@timestamp',asc)))" \
      "$4" 1700,700 40000
  }
  kib "$R/d_failure_http.json" 'request_id : "lb-failure-http-00042"' \
    "service,event,replica,upstream_host_name,upstream_host,request_path,path,status_code,duration_ms,request_id" b_kibana_http_envoy_and_replica.png
  kib "$R/d_failure_grpc.json" 'request_id : "lb-failure-grpc-00042"' \
    "service,event,replica,upstream_host_name,upstream_host,request_path,rpc_method,grpc_code,duration_ms,request_id" b_kibana_grpc_envoy_and_replica.png
  kib "$R/d_failure_http.json" 'event : "envoy_access" and response_flags : "UF"' \
    "upstream_host_name,upstream_host,status_code,grpc_code,response_flags,duration_ms,request_id" d_kibana_failure_uf.png
fi
