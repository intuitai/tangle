# src/tangle/cli.py

import argparse

import uvicorn

from tangle.config import TangleConfig
from tangle.monitor import TangleMonitor
from tangle.server.app import create_app


def main():
    parser = argparse.ArgumentParser(description="Tangle sidecar server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8090)
    args = parser.parse_args()

    config = TangleConfig(server_host=args.host, server_port=args.port)
    monitor = TangleMonitor(config=config)
    app = create_app(monitor)

    monitor.start_background()
    try:
        uvicorn.run(app, host=args.host, port=args.port)
    finally:
        monitor.stop()


if __name__ == "__main__":
    main()
