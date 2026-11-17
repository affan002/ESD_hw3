#!/bin/sh
# One-shot setup for the log pipeline (Part C), run by the `logs-setup` Compose
# service (curlimages/curl). Idempotent: every step overwrites, so it's safe to
# re-run on each `docker compose up`.
#
#   1. Elasticsearch: ILM poll interval, retention policy, index template
#      (must exist before Filebeat writes, or the data stream would get a
#      guessed mapping and no retention).
#   2. Kibana: import the data view and saved searches, make the data view default.
set -eu

ES="${ES_URL:-http://elasticsearch:9200}"
KB="${KIBANA_URL:-http://kibana:5601}"
DIR="${CONFIG_DIR:-/monitoring}"

say() { echo "[logs-setup] $*"; }

say "waiting for Elasticsearch at $ES"
until curl -fs "$ES/_cluster/health?wait_for_status=yellow&timeout=5s" >/dev/null; do sleep 2; done

# ILM checks policies every 10 minutes by default; check every minute so the
# short demo retention (monitoring/elasticsearch/ilm-policy.json) is visible.
say "cluster setting indices.lifecycle.poll_interval=1m"
curl -fsS -X PUT "$ES/_cluster/settings" -H 'Content-Type: application/json' \
  -d '{"persistent":{"indices.lifecycle.poll_interval":"1m"}}'; echo

# Disk watermarks for this single-node local stack (decision A3-17). The defaults
# (85 % low / 90 % high / 95 % flood stage) are measured on the HOST disk, because
# the es-data volume lives there. On a nearly full laptop disk, Elasticsearch then
# refused to place each new backing index after a rollover, and logging stopped,
# although our logs are only a few MB. It still blocks writes at 97 %.
say "cluster setting disk watermarks 93% / 95% / 97%"
curl -fsS -X PUT "$ES/_cluster/settings" -H 'Content-Type: application/json' \
  -d '{"persistent":{"cluster.routing.allocation.disk.watermark.low":"93%","cluster.routing.allocation.disk.watermark.high":"95%","cluster.routing.allocation.disk.watermark.flood_stage":"97%"}}'; echo

say "ILM policy sensor-logs-policy"
curl -fsS -X PUT "$ES/_ilm/policy/sensor-logs-policy" -H 'Content-Type: application/json' \
  --data-binary "@$DIR/elasticsearch/ilm-policy.json"; echo

say "index template sensor-logs"
curl -fsS -X PUT "$ES/_index_template/sensor-logs" -H 'Content-Type: application/json' \
  --data-binary "@$DIR/elasticsearch/index-template.json"; echo

say "waiting for Kibana at $KB"
until curl -fs "$KB/api/status" 2>/dev/null | grep -q '"overall":{"level":"available"'; do sleep 3; done

# Kibana can report "available" and still drop the first requests while it finishes
# starting (seen on a memory-starved host: curl exit 52, empty reply), so retry.
say "importing Kibana saved objects (data view + saved searches)"
curl -fsS --retry 10 --retry-delay 5 --retry-all-errors -X POST "$KB/api/saved_objects/_import?overwrite=true" -H 'kbn-xsrf: true' \
  -F "file=@$DIR/kibana/saved-objects.ndjson"; echo

say "default data view = sensor-logs"
curl -fsS --retry 10 --retry-delay 5 --retry-all-errors -X POST "$KB/api/data_views/default" -H 'kbn-xsrf: true' -H 'Content-Type: application/json' \
  -d '{"data_view_id":"sensor-logs","force":true}'; echo

say "done"
