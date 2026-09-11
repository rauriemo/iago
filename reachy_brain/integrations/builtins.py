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
    registry.add_connection(Connection("notes", "local"))

    def register(
        module, account, name, description, inputs, handler, *, action="read", permission="allow"
    ):
        tool = Tool(
            module, account, name, description, inputs, {"type": "object"}, handler, action=action
        )
        registry.register(tool)
        policy.set(Rule(tool.key, action, permission))

    def charge(ctx, key, count, maximum):
        if ctx.budgets.get(key, 0) + count > maximum:
            raise ToolError("retrieval_budget")
        ctx.budgets[key] = ctx.budgets.get(key, 0) + count

    async def attach(ctx, frame_id, *, preview=False, region=None):
        charge(ctx, "previews" if preview else "details", 1, 24 if preview else 4)
        if not ctx.valid():
            raise ToolError("canceled")
        metadata = visual.describe(visual.get(frame_id))
        image = await asyncio.to_thread(visual.image_input, frame_id, region, thumbnail=preview)
        visual.get(frame_id)
        if not ctx.valid():
            raise ToolError("canceled")
        ctx.attachments.extend([{"type": "input_text", "text": str(metadata)}, image])
        ctx.evidence.append(metadata)
        return metadata

    async def capture(payload, ctx):
        source = payload.get("source")
        choices = [
            s for s in visual.sources.values() if s.enabled and (not source or source == s.id)
        ]
        if len(choices) != 1:
            raise ToolError("ambiguous" if choices else "unavailable")
        frames = visual.browse(source=choices[0].id, limit=1)["frames"]
        if not frames or visual.clock() - frames[0]["captured"] > 2:
            raise ToolError("unavailable_fresh_frame")
        return {"frame": await attach(ctx, frames[0]["id"])}

    async def search(payload, ctx):
        charge(ctx, "visual_calls", 1, 3)
        return visual.browse(
            source=payload.get("source"),
            start=payload.get("start", 0),
            end=payload.get("end", float("inf")),
            query=payload.get("query", ""),
            limit=8,
        )

    async def browse(payload, ctx):
        charge(ctx, "visual_calls", 1, 3)
        result = visual.browse(
            source=payload.get("source"),
            start=payload.get("start", 0),
            end=payload.get("end", float("inf")),
            cursor=payload.get("cursor", 0),
            limit=8,
        )
        for frame in result["frames"]:
            await attach(ctx, frame["id"], preview=True)
        return result

    async def inspect_frames(payload, ctx):
        charge(ctx, "visual_calls", 1, 3)
        return {"frames": [await attach(ctx, f) for f in payload["ids"]]}

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
        "Inspect the latest fresh frame from one live source; source ID required if ambiguous.",
        schema({"source": {"type": "string"}}),
        capture,
    )
    register(
        "visual",
        "session",
        "search_visual_history",
        "Search retained metadata/time/labels. This is not semantic image search.",
        schema(filters),
        search,
    )
    register(
        "visual",
        "session",
        "browse_visual_history",
        "Browse labeled thumbnails to find earlier visual evidence.",
        schema(filters),
        browse,
    )
    register(
        "visual",
        "session",
        "inspect_frames",
        "Read up to four detailed retained frames. Actual images accompany results.",
        schema(
            {"ids": {"type": "array", "items": {"type": "string"}, "maxItems": 4, "minItems": 1}},
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
        note_id = await asyncio.to_thread(notes.save, payload["text"])
        return {"note_id": note_id, "provider_ref": note_id}

    # Model-invoked persistence requires an explicit exact-action confirmation; UI is direct user input.
    register(
        "notes",
        "local",
        "save_idea",
        "Save an idea locally only after explicit user authorization.",
        schema({"text": {"type": "string", "maxLength": 12000}}, ["text"]),
        save_note,
        action="write",
        permission="confirm",
    )
