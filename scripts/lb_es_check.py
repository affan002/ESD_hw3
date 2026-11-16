#!/usr/bin/env python3
"""Check one run's traffic distribution against Envoy's access logs (Part D).

For a run summary written by lb_load.py, counts in Elasticsearch, over the run's
time range and its request ids (lb-<run>-<iface>-*):
  - Envoy access-log events (event envoy_access) by the endpoint Envoy picked
    (upstream_host_name) and by response code / flags;
  - the replicas' own events (http_request / grpc_request) by replica;
and puts them next to the client's counts (x-replica) and Prometheus's
(lb_run_stats.py output, if present). Counts are distinct request ids per bucket
(`documents` is the raw document count). Standard library only.

    python3 scripts/lb_es_check.py docs/results/a3/d_weighted_http.json
"""

import json
import sys
import urllib.request
from pathlib import Path

ES = "http://localhost:9200/sensor-logs/_search"


def search(body: dict) -> dict:
    req = urllib.request.Request(ES, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=20))


def main() -> int:
    path = Path(sys.argv[1])
    client = json.loads(path.read_text())
    prefix = f"lb-{client['run']}-{client['interface']}-"
    app_event = "http_request" if client["interface"] == "http" else "grpc_request"
    time_range = {"range": {"@timestamp": {"gte": client["start_utc"], "lte": client["end_utc"], "format": "strict_date_optional_time"}}}
    # The run's window is padded by 5 s at the end, for replies that finish after the last send.
    time_range["range"]["@timestamp"]["lte"] = f"{client['end_utc']}||+5s"

    def agg(event: str, field: str) -> dict:
        body = {"size": 0, "track_total_hits": True,
                "query": {"bool": {"filter": [time_range, {"term": {"event": event}}, {"prefix": {"request_id": prefix}}]}},
                "aggs": {"by": {"terms": {"field": field, "size": 20,
                                          "missing": 0 if field == "status_code" else "-"},
                                # every request id in the bucket: an exact distinct count
                                # (cardinality is approximate: 6000 ids read as 6001)
                                "aggs": {"ids": {"terms": {"field": "request_id", "size": 50000}}}}}}
        result = search(body)
        # Distinct request ids per bucket, so a log line shipped twice is counted once.
        buckets = {b["key"] if isinstance(b["key"], str) else str(b["key"]): len(b["ids"]["buckets"])
                   for b in result["aggregations"]["by"]["buckets"]}
        return {"total": sum(buckets.values()), "documents": result["hits"]["total"]["value"], **buckets}

    envoy_by_host = agg("envoy_access", "upstream_host_name")
    envoy_by_code = agg("envoy_access", "grpc_code" if client["interface"] == "grpc" else "status_code")
    envoy_by_flags = agg("envoy_access", "response_flags")
    app_by_replica = agg(app_event, "replica")

    server_path = path.with_name(path.stem + "_server.json")
    prometheus = None
    if server_path.exists():
        server = json.loads(server_path.read_text())
        prometheus = {r: v["requests"] for r, v in server["replicas"].items()}

    out = {
        "run": client["run"], "interface": client["interface"],
        "time_range_utc": [client["start_utc"], client["end_utc"]], "request_id_prefix": prefix,
        "client_sent": client["sent"], "client_ok_by_replica": client["replica_counts"],
        "prometheus_by_replica": prometheus,
        "envoy_access_log_by_upstream": envoy_by_host,
        "envoy_access_log_by_status": envoy_by_code,
        "envoy_access_log_by_response_flags": envoy_by_flags,
        "replica_logs_by_replica": app_by_replica,
    }
    print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
