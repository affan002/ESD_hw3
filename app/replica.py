"""Which replica this process is: A, B, C, … (decision A3-3).

Compose scaling (`deploy.replicas: 3`) gives every replica the same environment, so
the identity can't be configured per replica. The one per-replica fact is the
container name Compose assigns, `esd_hw3-app-<N>`. Docker's embedded DNS server
(127.0.0.11) answers a reverse (PTR) lookup of the container's own IP with that name;
`socket.gethostbyaddr` can't be used because /etc/hosts maps the IP to the container
id first. N = 1, 2, 3 becomes A, B, C.

Order: the REPLICA_ID setting, then the container name, then the hostname.
"""

import logging
import re
import socket
import struct

log = logging.getLogger("app.replica")

DOCKER_DNS = ("127.0.0.11", 53)
_CONTAINER_INDEX = re.compile(r"-(\d+)$")


def letter_for_index(index: int) -> str:
    """1 → A, 2 → B, … 26 → Z; beyond that the number itself."""
    return chr(ord("A") + index - 1) if 1 <= index <= 26 else str(index)


def replica_from_container_name(name: str) -> str | None:
    """`esd_hw3-app-2.esd_hw3_default` → "B"; None if the name has no Compose index."""
    match = _CONTAINER_INDEX.search(name.split(".", 1)[0])
    return letter_for_index(int(match.group(1))) if match else None


def _encode_name(name: str) -> bytes:
    return b"".join(bytes([len(p)]) + p.encode() for p in name.split(".")) + b"\0"


def _read_name(data: bytes, offset: int) -> tuple[str, int]:
    """Decode a (possibly compressed) DNS name at `offset`; returns it and the offset after it."""
    labels, end = [], None
    while True:
        length = data[offset]
        if length & 0xC0 == 0xC0:  # pointer to a name earlier in the message
            if end is None:
                end = offset + 2
            offset = ((length & 0x3F) << 8) | data[offset + 1]
            continue
        if length == 0:
            return ".".join(labels), (end if end is not None else offset + 1)
        labels.append(data[offset + 1:offset + 1 + length].decode())
        offset += 1 + length


def reverse_lookup(ip: str, server: tuple[str, int] = DOCKER_DNS, timeout_s: float = 1.0) -> str | None:
    """PTR name for `ip` from `server`, or None (standard library only)."""
    query = struct.pack(">HHHHHH", 0x5EA5, 0x0100, 1, 0, 0, 0)
    query += _encode_name(".".join(reversed(ip.split("."))) + ".in-addr.arpa") + struct.pack(">HH", 12, 1)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(timeout_s)
        sock.sendto(query, server)
        data, _ = sock.recvfrom(1024)
    if struct.unpack(">H", data[6:8])[0] == 0:  # no answers
        return None
    _, offset = _read_name(data, 12)  # skip the question
    offset += 4
    _, offset = _read_name(data, offset)  # answer owner name
    rtype, _, _, _ = struct.unpack(">HHIH", data[offset:offset + 10])
    return _read_name(data, offset + 10)[0] if rtype == 12 else None


def resolve_replica_id(configured: str = "") -> str:
    if configured:
        return configured
    hostname = socket.gethostname()
    try:
        name = reverse_lookup(socket.gethostbyname(hostname))
    except OSError:  # not in Docker (no 127.0.0.11), or no answer in time
        name = None
    replica = replica_from_container_name(name) if name else None
    if replica is None:
        log.warning("replica id: no Compose container name found; using the hostname",
                    extra={"event": "replica_id_fallback"})
        return hostname
    return replica
