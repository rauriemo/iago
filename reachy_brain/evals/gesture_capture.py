"""Capture continuous local gesture observations; no camera or physical qualification."""

import asyncio
import json
import math
import time

import anyio

from .gesture_observation import GestureSnapshot, compare_gestures
from .speech_observation import validate_snapshot


async def record(
    client, output, *, duration=1800, interval=2, clock=time.monotonic, sleep=asyncio.sleep
):
    if (
        not all(math.isfinite(v) for v in (duration, interval))
        or not 0 < duration <= 7200
        or not 1 <= interval <= 30
    ):
        raise ValueError("invalid_gesture_capture_duration")
    started = clock()
    previous = None
    count = size = 0
    async with await anyio.open_file(output, "x", encoding="utf-8") as stream:

        async def append(value):
            nonlocal size
            line = json.dumps(value, allow_nan=False, separators=(",", ":")) + "\n"
            if size + len(line.encode()) > 32 * 1024 * 1024:
                raise ValueError("gesture_capture_limit")
            await stream.write(line)
            await stream.flush()
            size += len(line.encode())

        await append(
            {
                "type": "start",
                "version": 1,
                "duration": duration,
                "interval": interval,
                "physical_qualification": False,
            }
        )
        try:
            while True:
                body = bytearray()
                async with client.stream("GET", "/api/gesture-activity") as response:
                    response.raise_for_status()
                    async for chunk in response.aiter_bytes():
                        if len(body) + len(chunk) > 512 * 1024:
                            raise ValueError("gesture_response_limit")
                        body.extend(chunk)
                current = GestureSnapshot.model_validate_json(body)
                validate_snapshot(current)
                if previous is not None:
                    compare_gestures(previous, current)
                await append({"type": "sample", "observation": current.model_dump()})
                previous = current
                count += 1
                elapsed = clock() - started
                if not math.isfinite(elapsed) or elapsed < 0:
                    raise ValueError("gesture_capture_clock_changed")
                if elapsed >= duration:
                    break
                await sleep(min(interval, duration - elapsed))
        except BaseException as exc:
            await append({"type": "incomplete", "samples": count, "error": type(exc).__name__})
            raise
        await append(
            {"type": "complete_capture", "samples": count, "physical_qualification": False}
        )
    return count


def main():
    import argparse
    import os
    from pathlib import Path
    from urllib.parse import urlsplit

    import httpx

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8765")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--duration", type=float, default=1800)
    parser.add_argument("--interval", type=float, default=2)
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
        parser.error("Set IAGO_LOCAL_CONTROL_TOKEN privately")

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
        parser.exit(
            2, f"Gesture capture incomplete ({type(exc).__name__}); no qualification claimed.\n"
        )
    print("Controller observations saved; independent live-camera evaluation remains required.")


if __name__ == "__main__":
    main()
