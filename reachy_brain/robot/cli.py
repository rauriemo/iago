"""Launch the robot-local edge service beside an existing Reachy daemon."""

import argparse
import os

import uvicorn
from dotenv import load_dotenv

from .service import create_edge_app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8877)
    parser.add_argument("--tls-cert")
    parser.add_argument("--tls-key")
    args = parser.parse_args()
    load_dotenv(".env", override=False)
    token = os.environ.get("IAGO_EDGE_TOKEN", "")
    if len(token) < 24:
        parser.error(
            "Set a private IAGO_EDGE_TOKEN of at least 24 characters; do not pass it on the command line"
        )
    if args.host not in {"127.0.0.1", "localhost", "::1"} and not (args.tls_cert and args.tls_key):
        parser.error("Network binding requires --tls-cert and --tls-key")
    if bool(args.tls_cert) != bool(args.tls_key):
        parser.error("Provide both TLS files")
    uvicorn.run(
        create_edge_app(token),
        host=args.host,
        port=args.port,
        access_log=False,
        ssl_certfile=args.tls_cert,
        ssl_keyfile=args.tls_key,
        ws_max_size=8192,
        ws_max_queue=8,
        timeout_graceful_shutdown=3,
    )


if __name__ == "__main__":
    main()
