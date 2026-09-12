"""Capture bounded runtime observations; this command does not score a soak."""

import argparse
import asyncio
import json
import os
import time
from pathlib import Path
from urllib.parse import urlsplit

import anyio
import httpx


async def record(
    client, output, *, duration=5400, interval=5, clock=time.monotonic, sleep=asyncio.sleep
):
    if not 0 < duration <= 7200 or not 1 <= interval <= 30:
        raise ValueError("invalid_capture_duration")
    started = clock()
    samples = 0
    written = 0
    owner = None
    async with await anyio.open_file(output, "x", encoding="utf-8") as stream:

        async def append(value):
            nonlocal written
            line = json.dumps(value, allow_nan=False, separators=(",", ":")) + "\n"
            size = len(line.encode("utf-8"))
            if size > 131072 or written + size > 32 * 1024 * 1024:
                raise ValueError("workload_capture_limit")
            await stream.write(line)
            await stream.flush()
            written += size

        await append(
            {
                "type": "start",
                "version": 1,
                "duration": duration,
                "interval": interval,
                "physical_qualification": False,
                "scope": "Observed runtime metadata; no soak pass",
            }
        )
        try:
            while True:
                body = bytearray()
                async with client.stream("GET", "/api/workload-status") as response:
                    response.raise_for_status()
                    async for chunk in response.aiter_bytes():
                        if len(body) + len(chunk) > 131072:
                            raise ValueError("workload_response_limit")
                        body.extend(chunk)
                value = json.loads(body)
                if value.get("version") != 1 or value.get("physical_qualification") is not False:
                    raise ValueError("invalid_workload_snapshot")
                if owner is None:
                    owner = value["owner"]
                if owner != value["owner"]:
                    raise ValueError("workload_owner_changed")
                elapsed = clock() - started
                await append({"type": "sample", "elapsed": elapsed, "observation": value})
                samples += 1
                if elapsed >= duration:
                    break
                await sleep(min(interval, duration - elapsed))
        except BaseException as exc:
            # Preserve incomplete evidence without response bodies, URLs or credentials.
            await append({"type": "incomplete", "samples": samples, "error": type(exc).__name__})
            raise
        await append(
            {
                "type": "complete_capture",
                "samples": samples,
                "elapsed": clock() - started,
                "physical_qualification": False,
            }
        )
    return samples


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8765")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--duration", type=float, default=5400)
    parser.add_argument("--interval", type=float, default=5)
    args = parser.parse_args()
    url = urlsplit(args.url)
    if (
        url.scheme != "http"
        or url.hostname not in {"127.0.0.1", "localhost", "::1"}
        or url.username
        or url.password
        or url.query
        or url.fragment
        or url.path not in {"", "/"}
    ):
        parser.error("Use the application's loopback HTTP origin")
    token = os.environ.get("IAGO_LOCAL_CONTROL_TOKEN", "")
    if not token:
        parser.error("Set IAGO_LOCAL_CONTROL_TOKEN privately; do not pass it in the URL")

    async def run():
        async with httpx.AsyncClient(
            base_url=args.url,
            headers={"Authorization": "Bearer " + token},
            timeout=5,
            trust_env=False,
            follow_redirects=False,
        ) as client:
            await record(client, args.output, duration=args.duration, interval=args.interval)

    try:
        asyncio.run(run())
    except (Exception, KeyboardInterrupt) as exc:
        parser.exit(2, f"Capture incomplete ({type(exc).__name__}); no qualification claimed.\n")
    print(
        "Runtime capture saved; task coverage and physical qualification still require evaluation."
    )


if __name__ == "__main__":
    main()
