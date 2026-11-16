#!/usr/bin/env python3
"""Print every stored log line for one request id: Envoy's access-log event and the
replica's own event, from Elasticsearch (the same search as Kibana's saved searches
"Envoy + replica: HTTP request / gRPC call"). Standard library only.

    python3 scripts/lb_log_pair.py lb-baseline-http-00042 [lb-round-robin-grpc-00042 …]
"""

import json
import sys
import urllib.request

ES = "http://localhost:9200/sensor-logs/_search"
FIELDS = ("@timestamp", "service", "event", "replica", "upstream_host_name", "upstream_host", "listener",
          "method", "request_path", "path", "rpc_method", "status_code", "grpc_code", "duration_ms",
          "upstream_ms", "response_flags", "request_id", "container")

for request_id in sys.argv[1:]:
    body = json.dumps({"size": 20, "sort": [{"@timestamp": "asc"}],
                       "query": {"term": {"request_id": request_id}}}).encode()
    req = urllib.request.Request(ES, data=body, headers={"Content-Type": "application/json"})
    hits = json.load(urllib.request.urlopen(req, timeout=10))["hits"]["hits"]
    print(f'KQL: request_id : "{request_id}"   -> {len(hits)} documents')
    for h in hits:
        src = h["_source"]
        doc = {k: src[k] for k in FIELDS if k in src and k != "container"}
        doc["container.name"] = src.get("container", {}).get("name")
        print("  " + json.dumps(doc))
    print()
