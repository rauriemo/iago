import argparse
import secrets
import threading
import webbrowser

import uvicorn

from reachy_brain.config import Settings


def main():
    parser = argparse.ArgumentParser(description="Launch Iago locally")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--port", type=int)
    args = parser.parse_args()
    settings = Settings()
    if args.port:
        settings.desktop_port = args.port
    if settings.deployment_mode == "fake":
        parser.error(
            "Fake media is available through the acceptance harness, not a production launch profile"
        )
    if settings.desktop_host not in {"127.0.0.1", "localhost"}:
        parser.error("Desktop must bind loopback")
    from reachy_brain.web.app import create_app

    token = secrets.token_urlsafe(32)
    app = create_app(settings, token)
    url = f"http://127.0.0.1:{settings.desktop_port}/#token={token}"
    if not args.no_browser:
        timer = threading.Timer(1.2, lambda: webbrowser.open(url))
        timer.daemon = True
        timer.start()
    # Local capability token is not a provider credential; do not persist it in logs.
    print(f"Iago listening at http://127.0.0.1:{settings.desktop_port}. Ctrl+C stops the server.")
    uvicorn.run(
        app,
        host=settings.desktop_host,
        port=settings.desktop_port,
        access_log=False,
        ws_max_size=2**20,
        ws_max_queue=8,
        # Bound shutdown when a browser/Windows transport leaves a closing socket pending.
        timeout_graceful_shutdown=3,
    )


if __name__ == "__main__":
    main()
