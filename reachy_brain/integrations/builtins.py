"""Built-in tools registered outside the conversation core, through E1 policy/execution."""

import asyncio

from .registry import Connection, Rule, Tool, ToolError


def schema(properties, required=()):
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


def register_builtins(registry, policy, visual, projects, notes):
    registry.add_connection(Connection("visual", "session"))
    registry.add_connection(Connection("documents", "active"))
    registry.add_connection(Connection("notes", "local", identity=notes.identity))

    def register(
        module, account, name, description, inputs, handler, *, action="read", permission="allow"
    ):
        tool = Tool(
            module, account, name, description, inputs, {"type": "object"}, handler, action=action
        )
        registry.register(tool)
        policy.set(Rule(tool.key, action, permission))
        return tool

    def charge(ctx, key, count, maximum):
        if ctx.budgets.get(key, 0) + count > maximum:
            raise ToolError("retrieval_budget")
        ctx.budgets[key] = ctx.budgets.get(key, 0) + count

    async def attach(ctx, frame_id, *, preview=False, region=None):
        charge(ctx, "previews" if preview else "details", 1, 24 if preview else 4)
        if not ctx.valid():
            raise ToolError("canceled")
        metadata = visual.describe(visual.get(frame_id))
        if region is not None:
            metadata["region"] = list(region)
        image = await visual.image_input_async(frame_id, region, thumbnail=preview)
        visual.get(frame_id)
        if not ctx.valid():
            raise ToolError("canceled")
        ctx.attachments.extend([{"type": "input_text", "text": str(metadata)}, image])
        ctx.evidence.append(metadata)
        return metadata

    async def capture(payload, ctx):
        preview = payload.get("detail", "full") == "preview"
        region = payload.get("region")
        if region is not None:
            x, y, width, height = region
            if preview or min(x, y) < 0 or min(width, height) <= 0:
                raise ToolError("invalid_region")
        source = payload.get("source")
        choices = [
            s for s in visual.sources.values() if s.enabled and (not source or source == s.id)
        ]
        if len(choices) != 1:
            raise ToolError("ambiguous" if choices else "unavailable")
        selected = choices[0]
        if selected.kind == "upload":
            raise ToolError("unavailable_live_source")
        generation, requested = selected.generation, visual.clock()
        initial = visual.browse(source=selected.id, limit=1)["frames"]
        previous = initial[0]["id"] if initial else None
        request_capture = visual.capture_hooks.get(selected.id)
        if request_capture is not None and not request_capture():
            raise ToolError("unavailable_live_source")
        deadline = asyncio.get_running_loop().time() + 2
        while True:
            if not ctx.valid():
                raise ToolError("canceled")
            current = visual.sources.get(selected.id)
            if current is not selected or not selected.enabled or selected.generation != generation:
                raise ToolError("stale_source")
            frames = visual.browse(source=selected.id, limit=1)["frames"]
            if frames:
                frame = frames[0]
                if (
                    frame["id"] != previous
                    and frame["arrived"] >= requested
                    and (not frame["capture_time_known"] or frame["captured"] >= requested)
                ):
                    return {
                        "frame": await attach(ctx, frame["id"], preview=preview, region=region),
                        "detail": "preview" if preview else "full",
                    }
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise ToolError("unavailable_fresh_frame")
            await asyncio.sleep(min(0.05, remaining))

    async def search(payload, ctx):
        charge(ctx, "visual_calls", 1, 3)
        return visual.search(
            source=payload.get("source"),
            start=payload.get("start", 0),
            end=payload.get("end", float("inf")),
            query=payload.get("query", ""),
            event_types=payload.get("event_types", []),
            object_labels=payload.get("object_labels", []),
            near=payload.get("near"),
        )

    async def browse(payload, ctx):
        charge(ctx, "visual_calls", 1, 3)
        result = visual.browse(
            source=payload.get("source"),
            start=payload.get("start", 0),
            end=payload.get("end", float("inf")),
            cursor=payload.get("cursor", 0),
            limit=payload.get("limit", 8),
            sampling=payload.get("sampling", "representative"),
        )
        for frame in result["frames"]:
            await attach(ctx, frame["id"], preview=True)
        return result

    async def inspect_frames(payload, ctx):
        charge(ctx, "visual_calls", 1, 3)
        preview = payload.get("detail", "full") == "preview"
        key, maximum = ("previews", 24) if preview else ("details", 4)
        if ctx.budgets.get(key, 0) + len(payload["ids"]) > maximum:
            raise ToolError("retrieval_budget")
        for frame_id in payload["ids"]:
            visual.get(frame_id)
        return {
            "frames": [await attach(ctx, f, preview=preview) for f in payload["ids"]],
            "detail": "preview" if preview else "full",
        }

    async def inspect_region(payload, ctx):
        charge(ctx, "visual_calls", 1, 3)
        return {
            "frame": await attach(ctx, payload["id"], region=payload["region"]),
            "region": payload["region"],
        }

    async def pins(payload, ctx):
        return {"pins": [visual.describe(f) for f in visual.frames.values() if f.pin]}

    filters = {
        "source": {"type": "string"},
        "start": {"type": "number"},
        "end": {"type": "number"},
        "query": {"type": "string", "maxLength": 200},
        "cursor": {"type": "integer", "minimum": 0},
    }
    register(
        "visual",
        "session",
        "capture_now",
        "Wait up to two seconds for a new archived frame from one live source; source ID required if ambiguous. Detail defaults to full retained resolution; preview is for navigation. Optional region uses original x,y,width,height pixels and requires full detail. Physical timing uncertainty remains in the evidence.",
        schema(
            {
                "source": {"type": "string"},
                "detail": {"type": "string", "enum": ["preview", "full"]},
                "region": {
                    "type": "array",
                    "items": {"type": "integer", "minimum": 0, "maximum": 8192},
                    "minItems": 4,
                    "maxItems": 4,
                },
            }
        ),
        capture,
    )
    register(
        "visual",
        "session",
        "search_visual_history",
        "Search retained labels, pins, speech-interval keywords and detector event/object metadata, ranked by text matches, optional near timestamp, measured thumbnail sharpness, then recency. Event/object filters match any supplied value within each filter. Speech keywords locate nearby frames but do not describe their pixels. This is not semantic image or full-transcript search; results report coverage gaps.",
        schema(
            {
                **{key: value for key, value in filters.items() if key != "cursor"},
                "event_types": {
                    "type": "array",
                    "items": {"type": "string", "maxLength": 80},
                    "maxItems": 16,
                },
                "object_labels": {
                    "type": "array",
                    "items": {"type": "string", "maxLength": 80},
                    "maxItems": 16,
                },
                "near": {"type": "number", "minimum": 0},
            }
        ),
        search,
    )
    register(
        "visual",
        "session",
        "browse_visual_history",
        "Browse labeled thumbnails. Defaults to representative samples spanning the requested window; narrow start/end around a candidate to inspect neighbors. Use sampling recent with cursor for sequential pages. Representative sampling requires cursor zero and is not exhaustive.",
        schema(
            {
                **filters,
                "limit": {"type": "integer", "minimum": 1, "maximum": 8},
                "sampling": {"type": "string", "enum": ["representative", "recent"]},
            }
        ),
        browse,
    )
    register(
        "visual",
        "session",
        "inspect_frames",
        "Read up to four retained frames. Detail defaults to full retained resolution; preview is for navigation, not final text reading. Actual images and original provenance accompany results.",
        schema(
            {
                "ids": {"type": "array", "items": {"type": "string"}, "maxItems": 4, "minItems": 1},
                "detail": {"type": "string", "enum": ["full", "preview"]},
            },
            ["ids"],
        ),
        inspect_frames,
    )
    register(
        "visual",
        "session",
        "inspect_region",
        "Inspect a crop from the source-resolution original; coordinates x,y,width,height.",
        schema(
            {
                "id": {"type": "string"},
                "region": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "minItems": 4,
                    "maxItems": 4,
                },
            },
            ["id", "region"],
        ),
        inspect_region,
    )
    register(
        "visual",
        "session",
        "list_visual_pins",
        "List explicitly pinned references for this session.",
        schema({}),
        pins,
    )

    async def document_search(payload, ctx):
        charge(ctx, "document_calls", 1, 3)
        generation = projects.generation
        rows = await asyncio.to_thread(projects.search, payload["project"], payload["query"])
        if generation != projects.generation or not ctx.valid():
            raise ToolError("stale_project")
        charge(ctx, "passages", len(rows), 8)
        charge(ctx, "document_characters", sum(len(r["text"]) for r in rows), 12000)
        ctx.evidence.extend(rows)
        return {
            "passages": rows,
            "coverage": "Configured active project only; cite project/path/revision/locator.",
        }

    async def document_read(payload, ctx):
        charge(ctx, "document_calls", 1, 3)
        generation = projects.generation
        rows = await asyncio.to_thread(projects.read, payload["project"], payload["ids"])
        if generation != projects.generation or not ctx.valid():
            raise ToolError("stale_project")
        charge(ctx, "document_characters", sum(len(r["text"]) for r in rows), 12000)
        ctx.evidence.extend(rows)
        return {"passages": rows}

    register(
        "documents",
        "active",
        "search_project_documents",
        "Search passages in the active configured project. Project ID is provided in context.",
        schema(
            {"project": {"type": "string"}, "query": {"type": "string", "maxLength": 500}},
            ["project", "query"],
        ),
        document_search,
    )
    register(
        "documents",
        "active",
        "read_project_passages",
        "Read up to four revision-checked cited passages in the active project.",
        schema(
            {
                "project": {"type": "string"},
                "ids": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 4},
            },
            ["project", "ids"],
        ),
        document_read,
    )

    async def save_note(payload, ctx):
        note_id = await asyncio.to_thread(
            notes.save, payload["text"], operation_id=ctx.operation_id
        )
        return {"note_id": note_id, "provider_ref": note_id}

    async def note_operation_status(payload, ctx):
        return await asyncio.to_thread(notes.operation_status, payload["operation_id"])

    lookup = register(
        "notes",
        "local",
        "note_operation_status",
        "Check whether an operation's local note exists; absence remains uncertain. Returns no note text.",
        schema(
            {"operation_id": {"type": "string", "minLength": 1, "maxLength": 128}}, ["operation_id"]
        ),
        note_operation_status,
    )

    # Model-invoked persistence requires an explicit exact-action confirmation; UI is direct user input.
    save = register(
        "notes",
        "local",
        "save_idea",
        "Save an idea locally only after explicit user authorization.",
        schema({"text": {"type": "string", "maxLength": 12000}}, ["text"]),
        save_note,
        action="write",
        permission="confirm",
    )
    save.reconcile_tool = lookup.key
    save.version = "2"
