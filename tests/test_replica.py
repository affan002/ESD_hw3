"""Replica identity (A3-3): parsing the Compose container name, the PTR lookup, and
where the identity shows up (header, gRPC metadata, metric, log line)."""

import json
import logging
import socket
import struct
import threading

import pytest

from app import replica
from app.logging_setup import JsonFormatter, set_replica
from tests.test_metrics import sample


@pytest.mark.parametrize("name, expected", [
    ("esd_hw3-app-1.esd_hw3_default", "A"),
    ("esd_hw3-app-2", "B"),
    ("esd_hw3-app-3.esd_hw3_default", "C"),
    ("esd_hw3-app-4.esd_hw3_default", "D"),
    ("4ce6e7fdccf0", None),  # a bare container id: no Compose index
])
def test_replica_from_container_name(name, expected):
    assert replica.replica_from_container_name(name) == expected


def test_configured_replica_id_wins():
    assert replica.resolve_replica_id("Z") == "Z"


def _fake_dns_server(answer_name: str) -> tuple[socket.socket, tuple[str, int]]:
    """A one-shot UDP server answering a PTR query the way Docker's 127.0.0.11 does
    (the answer repeats the question name uncompressed)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))

    def answer():
        query, addr = sock.recvfrom(512)
        question = query[12:]
        header = query[:2] + struct.pack(">HHHHH", 0x8180, 1, 1, 0, 0)
        owner = question[:-4]  # the question name without type/class
        rdata = replica._encode_name(answer_name)
        rr = owner + struct.pack(">HHIH", 12, 1, 600, len(rdata)) + rdata
        sock.sendto(header + question + rr, addr)

    threading.Thread(target=answer, daemon=True).start()
    return sock, sock.getsockname()


def test_reverse_lookup_reads_docker_style_answer():
    sock, server = _fake_dns_server("esd_hw3-app-2.esd_hw3_default")
    try:
        assert replica.reverse_lookup("172.19.0.3", server=server) == "esd_hw3-app-2.esd_hw3_default"
    finally:
        sock.close()


def test_http_response_names_the_replica(client):
    resp = client.get("/health")
    assert resp.headers["x-replica"] == "T"
    assert sample("sensor_replica_info", replica="T") == 1


def test_json_log_lines_carry_the_replica():
    record = logging.LogRecord("app", logging.INFO, __file__, 1, "hello", None, None)
    set_replica("B")
    try:
        assert json.loads(JsonFormatter("sensor-api").format(record))["replica"] == "B"
    finally:
        set_replica(None)
    assert "replica" not in json.loads(JsonFormatter("simulator").format(record))
