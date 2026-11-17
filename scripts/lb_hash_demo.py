#!/usr/bin/env python3
"""Optional extra: consistent hashing for HTTP, then a fourth replica D (decision A3-18).

Steps (all through Envoy, HTTP GET /devices/dev-NNN, hash key = the path):
  1. create dev-001…dev-100 (idempotent seed);
  2. phase consistent-hash (A, B, C): ask for every device twice and record which
     replica answered (x-replica): the "before" map, and check it is sticky;
  3. `docker compose up -d --scale app=4` (esd_hw3-app-4 = replica D), wait until it is
     healthy, then phase consistent-hash-4 and wait until Envoy has D healthy;
  4. the "after" map: which devices changed replica, and where they went;
  5. back to three: phase consistent-hash, `--scale app=3`: every device should return
     to its "before" replica;
  6. restore round robin.
For comparison it also computes what naive modulo hashing (md5(id) % N) would move
when N goes from 3 to 4. Writes docs/results/a3/e_consistent_hash.json and .txt.

    .venv/bin/python scripts/lb_hash_demo.py
    .venv/bin/python scripts/lb_hash_demo.py /tmp/a3-try/hash   # practice run, writes /tmp/a3-try/hash.txt
"""

import hashlib
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
URL = "http://localhost:8000"
ADMIN = "http://localhost:9901/clusters"
DEVICES = [f"dev-{i:03d}" for i in range(1, 101)]
# Output prefix: the first argument, if given (for practice runs elsewhere).
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "docs" / "results" / "a3" / "e_consistent_hash"
lines: list[str] = []


def say(text: str = "") -> None:
    lines.append(text)
    sys.stderr.write(text + "\n")


def run(*cmd: str) -> None:
    subprocess.run(cmd, cwd=ROOT, check=True, capture_output=True)


def replica_for(device: str, n: int) -> str:
    req = urllib.request.Request(f"{URL}/devices/{device}", headers={"X-Request-ID": f"hash-{device}-{n}"})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.headers.get("x-replica", "?")
    except urllib.error.HTTPError as exc:
        return exc.headers.get("x-replica", "?")


def mapping(tag: str) -> dict[str, str]:
    first = {d: replica_for(d, 1) for d in DEVICES}
    second = {d: replica_for(d, 2) for d in DEVICES}
    unstable = [d for d in DEVICES if first[d] != second[d]]
    say(f"{tag}: {dict(sorted(Counter(first.values()).items()))}; asked twice, "
        f"{len(DEVICES) - len(unstable)}/{len(DEVICES)} devices got the same replica both times")
    return first


def healthy_hosts(cluster: str) -> set[str]:
    with urllib.request.urlopen(ADMIN, timeout=2) as resp:
        rows = [line.split("::") for line in resp.read().decode().splitlines()]
    hosts: dict[str, dict] = {}
    for r in rows:
        if len(r) == 4 and r[0] == cluster:
            hosts.setdefault(r[1], {})[r[2]] = r[3]
    return {h.get("hostname") for h in hosts.values() if h.get("health_flags") == "healthy"}


def wait_for(cond, what: str, timeout_s: float = 90) -> None:
    end = time.time() + timeout_s
    while time.time() < end:
        try:
            if cond():
                return
        except Exception:
            pass
        time.sleep(0.5)
    raise SystemExit(f"timed out waiting for {what}")


def container_healthy(name: str) -> bool:
    r = subprocess.run(["docker", "inspect", "-f", "{{.State.Health.Status}}", name], capture_output=True, text=True)
    return r.stdout.strip() == "healthy"


def phase(name: str) -> None:
    run("scripts/lb_policy.sh", name)


def modulo_moves() -> int:
    h = {d: int(hashlib.md5(d.encode()).hexdigest(), 16) for d in DEVICES}
    return sum(1 for d in DEVICES if h[d] % 3 != h[d] % 4)


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    run(sys.executable, "scripts/lb_load.py", "--seed", "--devices", str(len(DEVICES)))
    say(f"Consistent hashing demo, HTTP GET /devices/dev-NNN through Envoy, {len(DEVICES)} devices, "
        f"ring hash with 1024 points per host, keyed on the path; hosts placed on the ring by letter")
    say()

    phase("consistent-hash")
    wait_for(lambda: healthy_hosts("app_http") == {"A", "B", "C"}, "A, B, C healthy")
    before = mapping("3 replicas (A, B, C)")

    say("adding replica D: docker compose up -d --scale app=4")
    run("docker", "compose", "up", "-d", "--no-recreate", "--scale", "app=4", "app")
    wait_for(lambda: container_healthy("esd_hw3-app-4"), "esd_hw3-app-4 healthy")
    phase("consistent-hash-4")
    wait_for(lambda: healthy_hosts("app_http") == {"A", "B", "C", "D"}, "D healthy in Envoy")
    after = mapping("4 replicas (A, B, C, D)")

    moved = {d: (before[d], after[d]) for d in DEVICES if before[d] != after[d]}
    to_d = sum(1 for _, new in moved.values() if new == "D")
    say()
    say(f"devices that changed replica: {len(moved)}/{len(DEVICES)} "
        f"({100 * len(moved) / len(DEVICES):.0f} %; ideal for 3 -> 4 is 25 %), {to_d} of them to D")
    by_origin = Counter(old for old, _ in moved.values())
    say(f"  taken from: {dict(sorted(by_origin.items()))}")
    for d, (old, new) in sorted(moved.items()):
        say(f"  {d}: {old} -> {new}")
    say(f"for comparison, modulo hashing md5(id) % N would move {modulo_moves()}/{len(DEVICES)} devices from N=3 to N=4")
    say()

    say("removing D again: phase consistent-hash, docker compose up -d --scale app=3")
    phase("consistent-hash")
    wait_for(lambda: healthy_hosts("app_http") == {"A", "B", "C"}, "back to A, B, C")
    run("docker", "compose", "up", "-d", "--no-recreate", "--scale", "app=3", "app")
    restored = mapping("3 replicas again")
    back = sum(1 for d in DEVICES if restored[d] == before[d])
    say(f"devices back on their original replica: {back}/{len(DEVICES)}")

    phase("round-robin")
    say("restored: phase round-robin, 3 replicas")

    OUT.with_suffix(".json").write_text(json.dumps({
        "devices": len(DEVICES), "before": before, "after": after, "restored": restored,
        "moved": {d: {"from": o, "to": n} for d, (o, n) in moved.items()},
        "moved_count": len(moved), "moved_to_d": to_d, "modulo_moves": modulo_moves(),
        "back_on_original": back,
    }, indent=1) + "\n")
    OUT.with_suffix(".txt").write_text("\n".join(lines) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
