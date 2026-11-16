"""Entry point: `python -m app` runs one replica: the API (single uvicorn worker) on
API_PORT and, unless GRPC_ENABLED=false, the gRPC server on GRPC_PORT in the same process."""

import dataclasses
import os

import uvicorn

from app.config import load_settings
from app.logging_setup import set_replica, setup_logging
from app.main import create_app
from app.replica import resolve_replica_id


def main() -> None:
    os.environ.setdefault("GRPC_ENABLED", "true")
    settings = load_settings()
    setup_logging("sensor-api", settings.log_level)
    # Resolve once, after logging is set up (a fallback is logged), and tag every line.
    settings = dataclasses.replace(settings, replica_id=resolve_replica_id(settings.replica_id))
    set_replica(settings.replica_id)
    uvicorn.run(
        create_app(settings),
        host=settings.api_host,
        port=settings.api_port,
        log_config=None,
        access_log=False,
    )


if __name__ == "__main__":
    main()
