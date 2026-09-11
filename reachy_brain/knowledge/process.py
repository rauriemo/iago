"""Bounded output capture for the trusted disposable document-parser executable."""

import os
import subprocess
import sys
import threading


def capture_parser(command, *, timeout=30, limit=2 * 1024 * 1024):
    environment = {
        key: value
        for key, value in os.environ.items()
        if key.upper() in {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "LANG", "LC_ALL"}
    }
    environment["PYTHONUTF8"] = "1"
    data = bytearray()
    reader_error = []
    with subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env=environment,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    ) as process:

        def drain():
            try:
                while chunk := process.stdout.read1(min(65536, limit - len(data) + 1)):
                    if len(data) + len(chunk) > limit:
                        reader_error.append("over_limit")
                        process.kill()
                        return
                    data.extend(chunk)
            except OSError:
                reader_error.append("unreadable")
                if process.poll() is None:
                    process.kill()

        reader = threading.Thread(target=drain, daemon=True)
        reader.start()
        timed_out = False
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            process.kill()
            process.wait()
        except BaseException:
            if process.poll() is None:
                process.kill()
            process.wait()
            raise
        finally:
            # The fixed parser does not create descendants inheriting its output pipe.
            reader.join()
        if timed_out:
            return "timeout", b""
        if reader_error:
            return reader_error[0], b""
        if process.returncode:
            return "unreadable", b""
        return "ok", bytes(data)
