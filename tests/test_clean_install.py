"""Built wheel in an isolated native interpreter; no provider/device qualification."""

import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest


@pytest.mark.features("D1", "D4", "D5", "E1")
@pytest.mark.scenario("CLEAN-WHEEL-DESKTOP-STARTUP")
def test_wheel_installs_and_serves_without_robot_or_development_packages(tmp_path, record_property):
    uv = shutil.which("uv")
    assert uv, "uv is required for the locked clean-install acceptance check"
    root = Path(__file__).resolve().parents[1]
    env = {
        key: os.environ[key]
        for key in ("PATH", "SYSTEMROOT", "TEMP", "TMP", "USERPROFILE", "LOCALAPPDATA")
        if key in os.environ
    }
    commands = []

    def run(*args, cwd=root):
        command = [str(value) for value in args]
        commands.append(command)
        result = subprocess.run(
            command, cwd=cwd, env=env, capture_output=True, text=True, timeout=180
        )
        assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-4000:]
        return result.stdout

    artifacts = tmp_path / "artifacts"
    run(uv, "build", "--wheel", "--out-dir", artifacts)
    wheels = list(artifacts.glob("*.whl"))
    assert len(wheels) == 1
    requirements = artifacts / "requirements.txt"
    run(uv, "export", "--locked", "--no-dev", "--no-emit-project", "--output-file", requirements)
    isolated = tmp_path / "environment"
    run(uv, "venv", "--python", sys.executable, isolated)
    python = isolated / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    launcher = isolated / ("Scripts/iago.exe" if os.name == "nt" else "bin/iago")
    run(uv, "pip", "sync", "--python", python, "--require-hashes", requirements)
    run(uv, "pip", "install", "--python", python, "--no-deps", wheels[0])
    probe = run(
        python,
        "-I",
        "-c",
        """
import importlib.util,json,reachy_brain
from pathlib import Path
from reachy_brain.providers.costs import RateTable
print(json.dumps({"module":str(Path(reachy_brain.__file__).resolve()),
"optional":{name:importlib.util.find_spec(name) is not None for name in ["reachy_mini","mediapipe","pytest","gi"]},
"rates_date":str(RateTable.load().date)}))
""",
        cwd=tmp_path,
    )
    observed = json.loads(probe)
    assert Path(observed["module"]).is_relative_to(isolated)
    assert not any(observed["optional"].values())
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    command = [str(launcher), "--no-browser", "--port", str(port)]
    commands.append(command)
    process = subprocess.Popen(
        command,
        cwd=tmp_path,
        env={
            **env,
            "DATA_DIR": str(tmp_path / "runtime"),
            "DEPLOYMENT_MODE": "desktop",
            "IAGO_DEVELOPMENT_BUDGET": "0",
        },
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        deadline = time.monotonic() + 20
        while True:
            assert process.poll() is None, "Installed application exited before serving"
            try:
                with opener.open(f"http://127.0.0.1:{port}/", timeout=1) as response:
                    page = response.read().decode()
                break
            except (urllib.error.URLError, TimeoutError):
                assert time.monotonic() < deadline, "Installed application did not become ready"
                time.sleep(0.05)
        assert "Iago" in page
        for asset in ("app.js", "audio-worklet.js"):
            with opener.open(f"http://127.0.0.1:{port}/static/{asset}", timeout=2) as response:
                assert response.status == 200 and len(response.read()) > 100
        with pytest.raises(urllib.error.HTTPError) as denial:
            opener.open(f"http://127.0.0.1:{port}/api/status", timeout=2)
        assert denial.value.code == 401
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
    vision_requirements = artifacts / "requirements-vision.txt"
    run(
        uv,
        "export",
        "--locked",
        "--no-dev",
        "--no-emit-project",
        "--extra",
        "vision",
        "--output-file",
        vision_requirements,
    )
    run(uv, "pip", "sync", "--python", python, "--require-hashes", vision_requirements)
    run(uv, "pip", "install", "--python", python, "--no-deps", wheels[0])
    vision = json.loads(
        run(
            python,
            "-I",
            "-c",
            """
import importlib.util,json,mediapipe
from reachy_brain.web.app import create_app
from reachy_brain.config import Settings
app=create_app(Settings(_env_file=None,perception_enabled=True))
print(json.dumps({"mediapipe":mediapipe.__version__,"app_created":bool(app),
"robot_installed":importlib.util.find_spec("reachy_mini") is not None,
"test_packages":importlib.util.find_spec("pytest") is not None}))
""",
            cwd=tmp_path,
        )
    )
    assert vision["app_created"] and not vision["robot_installed"] and not vision["test_packages"]
    record_property("sample_count", 1)
    record_property(
        "expected",
        "Locked wheel installs and launches desktop without robot, detector or test packages; assets/auth boundary work",
    )
    record_property(
        "observed",
        json.dumps(
            {
                **observed,
                "commands": commands,
                "wheel_sha256": hashlib.sha256(wheels[0].read_bytes()).hexdigest(),
                "requirements_sha256": hashlib.sha256(requirements.read_bytes()).hexdigest(),
                "vision_requirements_sha256": hashlib.sha256(
                    vision_requirements.read_bytes()
                ).hexdigest(),
                "vision_extra": vision,
                "scope": "isolated interpreter on current host; no clean-OS, device, perception or acoustic qualification",
            }
        ),
    )
