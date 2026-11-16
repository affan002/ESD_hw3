# Part D predictions

Written **before** any Part D run (2026-11-16, after the Part B/C runs) and not edited afterwards.
Setup for every run: round-robin start, 50 req/s for 120 s per interface, one interface at a time, Envoy `--concurrency 2`, no client retries, no breaker, no Envoy retries.

Reference from Part C (round robin, no fault): client p95 10.0 ms (HTTP) / 9.1 ms (gRPC); about 16.7 req/s per replica; server time about 2–7 ms, so each replica has well under one request in flight on average.

## D1. Weighted round robin, A:B:C = 2:1:1
- Shares: about 50 / 25 / 25 %. gRPC (one connection, so one Envoy worker) should be almost exact (3000/1500/1500). HTTP (a new connection per request, spread over two workers that each keep their own weighted schedule) within about ±1 point.
- Latency: no measurable change in client p95 (still about 10 ms). A replica at 25 req/s is still far from busy, and a weight changes only who gets the work, not how fast it's done.
- Active requests: A's mean in flight about 2× B's or C's (Little's law: L = λ·W with the same W), but all well below 1.

## D2. Least request vs round robin, B delayed by 200 ms (both interfaces)
- Round robin: B still gets about 33 %, since RR ignores how busy a host is. B's mean in flight ≈ 16.7 req/s × 0.2 s ≈ 3.3. A third of all requests take ≥ 200 ms, so client p95 ≈ 205 ms.
- Least request (power of two choices): Envoy draws two hosts and picks the one with fewer active requests. B usually has more active requests, so it wins only when both draws are B (about 1/9) or on ties, so B's share falls to about 10 % and its in-flight count to about 1.
- Client p95: probably **still about 200 ms** under least request, because if roughly 10 % of requests still go to B, more than 5 % are slow. The mean and p90 should drop a lot (p90 back to about 10 ms). If B's share falls below 5 %, p95 drops to about 10 ms.
- B still passes every health check (`/health` and `grpc.health.v1` aren't delayed), so nothing removes it.

## D3. Failure and recovery (round robin, no delay): stop B during traffic, then start it
- Health checks: interval 2 s, timeout 1 s, unhealthy threshold 2, healthy threshold 2, `no_traffic_interval` 2 s. DNS refresh 5 s.
- After `docker stop`, B refuses connections. Until two health checks in a row fail, requests Envoy sends to B fail at once (HTTP 503 `UF`; gRPC `UNAVAILABLE`). Expected removal: 2–4 s after B stops listening, so about 1/3 × 50 req/s × 3 s ≈ 30–70 failed requests per interface. After removal, A and C each take about 50 %, with no errors.
- Recovery after `docker start`: about 2 s for the process to start, plus two passing checks (2–4 s), so about 4–7 s. It could be up to 5 s longer if B comes back on a new IP and Envoy must re-resolve the DNS name first. No client errors during recovery. Shares return to about 33 % each.
