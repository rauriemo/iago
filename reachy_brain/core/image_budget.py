"""Bound encoded image bytes before any model request, without decoding copies."""

from reachy_brain.integrations.registry import ToolError


def image_input_bytes(messages, maximum):
    pending = [messages]
    total, visited = 0, 0
    while pending:
        value = pending.pop()
        visited += 1
        if visited > 100000:
            raise ToolError("model_input_structure_limit")
        if isinstance(value, dict):
            if value.get("type") == "input_image":
                url = value.get("image_url")
                if (
                    not isinstance(url, str)
                    or not url.startswith("data:image/")
                    or ";base64," not in url[:80]
                    or not url.isascii()
                ):
                    raise ToolError("unbounded_model_image")
                total += len(url)
                if total > maximum:
                    raise ToolError("model_image_byte_limit")
            else:
                pending.extend(value.values())
        elif isinstance(value, (list, tuple)):
            pending.extend(value)
    return total
