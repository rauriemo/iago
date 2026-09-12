"""Versioned application wire contract; SDK device formats are converted at the edge."""

VERSION = 1


def compatible_version(message):
    return (
        isinstance(message, dict)
        and type(message.get("protocol_version")) is int
        and message["protocol_version"] == VERSION
    )


def capabilities(input_rate):
    if type(input_rate) is not int or not 8000 <= input_rate <= 192000:
        raise RuntimeError("edge_input_format_unavailable")
    return {
        "microphone": {"encoding": "pcm_s16le", "rate": input_rate, "channels": 1},
        "playback": {"encoding": "pcm_s16le", "rate": 24000, "channels": 1},
        "local_stop": True,
        "camera": {
            "encoding": "jpeg",
            "preview_max_dimension": 640,
            "reader_max_fps": 10,
            "capture_timestamps": False,
            "source_dimensions": None,
            "physical_qualification": "pending",
        },
    }


def validate_ready(message, *, session, connection):
    if not compatible_version(message):
        raise RuntimeError("edge_protocol_incompatible")
    if (
        message.get("type") != "ready"
        or message.get("session") != session
        or message.get("connection") != connection
        or type(message.get("stop_generation")) is not int
        or message["stop_generation"] < 0
    ):
        raise RuntimeError("edge_ownership_failed")
    offered = message.get("capabilities")
    if not isinstance(offered, dict) or offered.get("local_stop") is not True:
        raise RuntimeError("edge_required_capability_missing")
    microphone = offered.get("microphone")
    if not isinstance(microphone, dict):
        raise RuntimeError("edge_input_format_unavailable")
    expected = capabilities(microphone.get("rate"))
    for name in ("microphone", "playback"):
        value = offered.get(name)
        if (
            not isinstance(value, dict)
            or type(value.get("rate")) is not int
            or type(value.get("channels")) is not int
            or value != expected[name]
        ):
            raise RuntimeError("edge_audio_format_incompatible")
    return offered
