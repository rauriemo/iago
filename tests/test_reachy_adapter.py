"""Real adapter call sequence against an explicitly fake SDK receiver; no hardware claim."""

import inspect
from types import SimpleNamespace

import numpy as np
import pytest

from reachy_brain.robot.media import ReachyLocalMedia


@pytest.mark.features("D2", "D3", "P9")
@pytest.mark.scenario("REACHY-SDK-ADAPTER-CONTRACT")
def test_local_adapter_preserves_mic_on_flush_and_enforces_motion_limits():
    from reachy_mini import ReachyMini
    from reachy_mini.media.audio_gstreamer import GStreamerAudio
    from reachy_mini.media.media_manager import MediaManager

    assert "connection_mode" in inspect.signature(ReachyMini).parameters
    assert callable(GStreamerAudio.clear_player)
    assert callable(MediaManager.get_frame_jpeg)
    calls = []
    audio = SimpleNamespace(
        clear_player=lambda: calls.append("flush"),
        set_max_output_buffers=lambda n: calls.append(("buffers", n)),
    )
    media = SimpleNamespace(
        audio=audio,
        camera=object(),
        get_input_audio_samplerate=lambda: 16000,
        get_output_audio_samplerate=lambda: 16000,
        get_input_channels=lambda: 2,
        get_output_channels=lambda: 2,
        start_recording=lambda: calls.append("record"),
        start_playing=lambda: calls.append("play"),
        stop_recording=lambda: calls.append("stop_record"),
        stop_playing=lambda: calls.append("stop_play"),
        push_audio_sample=lambda data: calls.append(("pcm", data.shape, data.dtype)),
    )

    def factory(**kwargs):
        assert kwargs["connection_mode"] == "localhost_only" and kwargs["media_backend"] == "local"
        assert not kwargs["spawn_daemon"]
        return SimpleNamespace(
            media=media,
            cancel_move=lambda: calls.append("cancel_motion"),
            get_current_head_pose=lambda: np.eye(4),
            set_target=lambda **kwargs: calls.append("target"),
            __exit__=lambda *args: calls.append("close"),
        )

    adapter = ReachyLocalMedia(robot_factory=factory, motion=True)
    adapter.flush()
    assert "stop_record" not in calls and "stop_play" not in calls
    adapter.push_pcm(bytes(960))
    assert ("pcm", (320,), np.dtype("float32")) in calls
    assert adapter.target(np.eye(4))
    bad = np.eye(4)
    bad[0, 3] = 0.5
    with pytest.raises(ValueError, match="motion_limit"):
        adapter.target(bad)
    adapter.close()
    assert calls[-1] == "close"
    assert "stop_record" in calls and "stop_play" in calls
