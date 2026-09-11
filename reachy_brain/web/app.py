import asyncio
import contextlib
import json
import math
import secrets
import sqlite3
import time
import uuid
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import asdict, replace
from pathlib import Path

from anyio import BrokenResourceError, CancelScope, ClosedResourceError
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from reachy_brain.behavior.engine import BehaviorEngine, Event
from reachy_brain.behavior.settings import BehaviorConfiguration
from reachy_brain.behavior.window import TriggerWindow
from reachy_brain.config import DEFAULT_PERSONALITY, Settings
from reachy_brain.core.conversation import Conversation
from reachy_brain.integrations.builtins import register_builtins, schema
from reachy_brain.integrations.operations import OperationStore
from reachy_brain.integrations.preferences import ToolPreferences
from reachy_brain.integrations.registry import (
    ActionPolicy,
    CallContext,
    Connection,
    Rule,
    Tool,
    ToolError,
    ToolExecutor,
    ToolRegistry,
)
from reachy_brain.integrations.runtime import IntegrationRuntime
from reachy_brain.knowledge.index import ProjectIndex
from reachy_brain.providers.live import (
    AstraBrain,
    ElevenSpeech,
    OpenAISpeech,
    ProviderGate,
    Transcription,
)
from reachy_brain.providers.usage_storage import UsageStorage
from reachy_brain.resources import ResourceMonitor
from reachy_brain.robot.session import RobotSession
from reachy_brain.storage.notes import Notes
from reachy_brain.storage.personality import PersonalityStore
from reachy_brain.storage.recorder import TranscriptRecorder
from reachy_brain.storage.transcripts import Transcripts
from reachy_brain.storage.voice import VoiceSelection
from reachy_brain.vision.events import PerceptionEvents
from reachy_brain.vision.evidence import EventEvidence
from reachy_brain.vision.store import VisualStore
from reachy_brain.vision.worker import PerceptionWorker


def create_app(settings=None, token=None):
    settings = settings or Settings()
    token = token or secrets.token_urlsafe(32)
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    personality = PersonalityStore(settings.data_dir / "personality.json")
    settings.personality = personality.load(settings.personality)
    personality_lock = asyncio.Lock()
    voice_selection = VoiceSelection(settings.data_dir / "voice-selection.json")
    settings.tts_provider = voice_selection.load(settings.tts_provider)
    voice_selection_lock = asyncio.Lock()
    gate = ProviderGate(settings)
    usage_storage = UsageStorage(gate, settings.data_dir / "usage-accounting.json")
    brain = AstraBrain(settings, gate)
    voices = {"openai": OpenAISpeech(settings, gate), "elevenlabs": ElevenSpeech(settings, gate)}
    visual = VisualStore(
        max_bytes=settings.history_max_mib * 1024 * 1024,
        retention=settings.history_retention_seconds,
        pin_bytes=settings.pin_max_mib * 1024 * 1024,
        pin_count=settings.pin_max_images,
    )
    registry, policy = ToolRegistry(), ActionPolicy()
    projects = ProjectIndex(settings.data_dir / "projects.sqlite")
    notes = Notes(settings.data_dir / "notes.sqlite")
    transcripts = Transcripts(settings.data_dir / "transcripts.sqlite")
    recorder = TranscriptRecorder(transcripts)
    resource_monitor = ResourceMonitor()
    register_builtins(registry, policy, visual, projects, notes)
    integrations = IntegrationRuntime(
        registry, policy, settings.integration_config, defer_identity=True
    )
    indexing_tasks = {}
    forced_indexes = set()
    project_changes = set()
    running = False
    perception = {"worker": None, "latest": None, "error": None, "source": None}
    perception_events = PerceptionEvents()
    event_evidence = EventEvidence(visual)
    behaviors = BehaviorEngine()
    trigger_window = TriggerWindow()
    behavior_config = None

    async def poll_perception():
        while running:
            behavior_config.tick(time.time())
            latest = perception["latest"]
            if latest:
                source = visual.sources.get(latest["source"])
                if not source or not source.enabled or source.generation != latest["generation"]:
                    perception["latest"] = None
            worker = perception["worker"]
            result = worker.poll() if worker else None
            if result:
                if "error" in result:
                    perception["error"] = result["error"]
                else:
                    support_jpeg = result.pop("support_jpeg", None)
                    source = visual.sources.get(result["source"])
                    if (
                        source
                        and source.enabled
                        and source.generation == result["generation"]
                        and source.kind == "camera"
                    ):
                        perception["latest"] = result
                        events, observation = perception_events.update(
                            result, source, now=time.time()
                        )
                        core = active["conversation"]
                        support = None
                        if (
                            core
                            and observation
                            and core.thumbs.enabled
                            and core.thumbs.question
                            and observation.gesture in {"thumb_up", "thumb_down"}
                        ):
                            support = Event(
                                observation.id,
                                observation.source,
                                "thumb_observation",
                                observation.captured,
                                observation.confidence,
                                generation=observation.generation,
                                detector=observation.detector,
                            )
                        try:
                            attached = await event_evidence.attach(
                                events + ([support] if support else []),
                                support_jpeg,
                                uncertainty=result.get("uncertainty", 0),
                            )
                            if support:
                                observation = replace(observation, frames=attached[-1].frames)
                                events = attached[:-1]
                            else:
                                events = attached
                        except (ToolError, ValueError):
                            perception["error"] = "event_evidence_unavailable"
                        core = active["conversation"]
                        behaviors.mode = core.mode if core else "idle"
                        for event in events:
                            behaviors.offer(event, now=time.time())
                        if core and observation:
                            core.thumbs.observe(observation, now=time.time())
            core = active["conversation"]
            if core:
                response = core.thumbs.poll(now=time.time())
                if response:
                    await core.accept_thumb(response)
            if core:
                behaviors.mode = core.mode
                core.on_proactive_stop = lambda: behaviors.interrupt(
                    now=time.time(), backoff=behavior_config.value.interruption_backoff
                )
                if core.proactive_guard is not None and not core.proactive_guard():
                    await core.stop()
                # Keep provider startup off the detector polling loop.
                if core.mode in {"aware", "conversation"} and not trigger_window.busy:
                    intent = behaviors.take(
                        now=time.time(),
                        user_speaking=core.user_speaking,
                        output_busy=bool(core.task and not core.task.done()),
                    )
                    if intent:

                        def still_valid(intent=intent, core=core):
                            evidence = intent["evidence"]
                            source = visual.sources.get(evidence["source"])
                            return (
                                active["conversation"] is core
                                and not core.user_speaking
                                and source is not None
                                and source.enabled
                                and source.generation == evidence["generation"]
                                and behaviors.recheck(intent, now=time.time())
                            )

                        if still_valid():
                            if intent["action"] == "greet":
                                if core.mode == "aware":
                                    trigger_window.start(
                                        core,
                                        intent,
                                        still_valid,
                                        change_mode,
                                        behavior_config.value.conversation_window,
                                    )
                                else:
                                    await core.proactive(intent, still_valid)
                            elif intent["action"] == "bookmark":
                                for frame_id in intent["evidence"]["frames"][:2]:
                                    frame = visual.frames.get(frame_id)
                                    if frame and frame.source == intent["evidence"]["source"]:
                                        with contextlib.suppress(ToolError):
                                            visual.pin(
                                                frame_id, "Event: " + intent["evidence"]["kind"]
                                            )
            await asyncio.sleep(0.05)

    operations = None
    tool_preferences = None
    tool_preferences_lock = asyncio.Lock()
    executor = ToolExecutor(registry, policy, operations)
    active = {"control": None, "conversation": None, "stt": None, "stt_task": None, "audio": None}
    robot_mode = settings.deployment_mode in {"reachy_pc", "reachy_local"}
    robot_session = None
    page_owner = None

    async def notify_page(message):
        ws = active["control"]
        if ws:
            with contextlib.suppress(
                RuntimeError,
                WebSocketDisconnect,
                TimeoutError,
                BrokenResourceError,
                ClosedResourceError,
            ):
                async with asyncio.timeout(2):
                    await ws.send_json(message)

    async def robot_preview(source, generation, captured, jpeg, uncertainty, moving=False):
        if not source.enabled or source.generation != generation:
            return
        if perception["worker"] is None:
            perception["worker"] = PerceptionWorker(settings.perception_models)
        perception["source"] = source.id
        perception["worker"].offer(
            source.id, generation, captured, jpeg, uncertainty=uncertainty, moving=moving
        )

    registry.add_connection(Connection("questions", "session"))

    async def ask_question(payload, context):
        core = active["conversation"]
        if not core or core.session != context.session or not context.valid():
            raise ToolError("canceled")
        return await core.ask_yes_no(payload["question"])

    question_tool = Tool(
        "questions",
        "session",
        "ask_yes_no",
        "Speak an actual yes/no question and enable a context-bound thumb response after it is heard. Never use this for external-action permission.",
        schema({"question": {"type": "string", "minLength": 5, "maxLength": 500}}, ["question"]),
        {"type": "object"},
        ask_question,
    )
    registry.register(question_tool)
    policy.set(Rule(question_tool.key, "read", "allow"))

    async def close_stt():
        task, stt = active["stt_task"], active["stt"]
        active["stt_task"] = active["stt"] = None
        if task and task is not asyncio.current_task():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        if stt:
            await stt.close()

    async def change_mode(core, mode):
        if mode not in {"idle", "aware", "conversation"}:
            raise ToolError("invalid_mode")
        if robot_session:
            await robot_session.set_mode(mode)
            return
        await close_stt()
        try:
            if mode == "conversation":
                if not active["audio"]:
                    raise ToolError("microphone_not_ready")
                await core.select_voice()
                stt = Transcription(settings, gate)
                active["stt"] = stt
                await stt.start()

                async def receive_transcripts():
                    try:
                        async for event in stt.events():
                            await core.transcription_event(event)
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        pass  # Exception and clean unexpected EOF both mean recognition is lost.
                    if active["stt"] is stt and active["conversation"] is core:
                        # Detach before awaiting cleanup so no more microphone packets
                        # can enter a failed provider and stale cleanup cannot clear a replacement.
                        active["stt"] = active["stt_task"] = None
                        try:
                            await core.set_mode("aware")
                            await core.emit(
                                "error",
                                message="Transcription connection lost. Returned to Aware; restart conversation to retry.",
                            )
                        finally:
                            await stt.close()

            await core.set_mode(mode)
            if mode == "conversation":
                active["stt_task"] = asyncio.create_task(receive_transcripts())
        except BaseException:
            await close_stt()
            await core.set_mode("aware" if core.mode != "idle" else "idle")
            raise

    def schedule_index(project, *, force=False):
        if force:
            forced_indexes.add(project)
        if project in indexing_tasks and not indexing_tasks[project].done():
            return

        async def run():
            try:
                while running:
                    forced = project in forced_indexes
                    forced_indexes.discard(project)
                    try:
                        await asyncio.to_thread(
                            projects.reindex, project, lambda: running, force=forced
                        )
                    except (ToolError, OSError, sqlite3.Error):
                        pass  # Index status retains a bounded project-level failure code.
                    if project not in forced_indexes:
                        break
            finally:
                forced_indexes.discard(project)

        indexing_tasks[project] = asyncio.create_task(run())

    async def refresh_projects():
        while running:
            for project in await asyncio.to_thread(projects.projects):
                schedule_index(project["id"])
            # Leave extraction/commit time inside the five-second stable-change target.
            # schedule_index coalesces a project's refresh while its worker is active.
            await asyncio.sleep(1)

    @asynccontextmanager
    async def lifespan(app):
        nonlocal operations, running, robot_session, behavior_config, tool_preferences
        async with AsyncExitStack() as resources:
            resources.push_async_callback(usage_storage.close)
            # Register every release before the next acquisition can fail. MCP adapters
            # isolate their SDK task groups in their own connection-owner tasks.
            resources.push_async_callback(brain.close)
            resources.push_async_callback(voices["openai"].close)
            await usage_storage.start()
            await recorder.start()
            resources.push_async_callback(recorder.close)
            behavior_config = BehaviorConfiguration(settings.data_dir / "behaviors.json", behaviors)
            operations = OperationStore(
                settings.data_dir / "operations.sqlite",
                max_bytes=settings.operation_max_mib * 1024 * 1024,
                retention_days=settings.operation_retention_days,
            )
            resources.callback(operations.close)
            executor.operations = operations
            await executor.journal(operations.cleanup)
            await resources.enter_async_context(integrations)
            tool_preferences = ToolPreferences(
                settings.data_dir / "tool-preferences.json",
                registry,
                policy,
                settings.integration_config,
            )
            for module in integrations.modules:
                if hasattr(module, "identify"):
                    await module.identify(registry, policy)
            resources.push_async_callback(executor.close)
            resources.push_async_callback(close_stt)

            async def close_detector():
                if perception["worker"]:
                    await asyncio.to_thread(perception["worker"].close)

            resources.push_async_callback(close_detector)
            if robot_mode:
                core = Conversation(settings, brain, voices, executor, visual, notify_page)
                core.record = recorder.submit
                core.record_snapshot = recorder.snapshot
                core.project_context = lambda: projects.active
                core.project_context_ready = lambda: not project_changes
                core.project_context_generation = lambda: projects.generation
                core.runtime_capabilities = lambda: frozenset(integrations.capabilities)
                active["conversation"] = core
                robot_session = RobotSession(
                    settings, gate, core, notify_page, on_preview=robot_preview
                )
                core.send = robot_session.send
                resources.push_async_callback(robot_session.close)
                await robot_session.start()

            async def end_conversation():
                if active["conversation"]:
                    await active["conversation"].set_mode("idle")

            resources.push_async_callback(end_conversation)
            resources.push_async_callback(trigger_window.cancel)
            running = True

            async def clean_operations():
                while running:
                    await asyncio.sleep(settings.operation_cleanup_seconds)
                    try:
                        await executor.journal(operations.cleanup)
                    except Exception:
                        executor.diagnostics.append({"error": "operation_cleanup_failed"})
                        del executor.diagnostics[:-100]

            journal_cleaner = asyncio.create_task(clean_operations())

            async def sample_resources():
                while running:
                    await asyncio.to_thread(resource_monitor.sample)
                    await asyncio.sleep(5)

            resource_sampler = asyncio.create_task(sample_resources())
            watcher = asyncio.create_task(refresh_projects())
            detector_poll = asyncio.create_task(poll_perception())
            try:
                yield
            finally:
                running = False
                watcher.cancel()
                detector_poll.cancel()
                journal_cleaner.cancel()
                resource_sampler.cancel()
                await asyncio.gather(
                    watcher,
                    detector_poll,
                    journal_cleaner,
                    resource_sampler,
                    return_exceptions=True,
                )
                await asyncio.gather(*indexing_tasks.values(), return_exceptions=True)

    app = FastAPI(lifespan=lifespan)
    app.state.token, app.state.settings = token, settings
    app.state.visual, app.state.active = visual, active
    app.state.projects, app.state.executor = projects, executor
    app.state.recorder = recorder
    app.add_middleware(
        TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"]
    )
    static = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=static), name="static")

    def authorized(request):
        expected = "Bearer " + token
        supplied = request.headers.get("authorization", "")
        if not secrets.compare_digest(supplied, expected):
            raise HTTPException(401, "Local session authorization required")
        origin = request.headers.get("origin")
        if origin and origin not in {
            f"http://127.0.0.1:{settings.desktop_port}",
            f"http://localhost:{settings.desktop_port}",
        }:
            raise HTTPException(403, "Origin rejected")

    def conversation():
        if not active["conversation"]:
            raise HTTPException(409, "Open the conversation page first")
        return active["conversation"]

    @app.exception_handler(ToolError)
    async def tool_error(request, exc):
        from fastapi.responses import JSONResponse

        return JSONResponse({"error": exc.code}, status_code=409)

    @app.get("/")
    async def index():
        return FileResponse(static / "index.html")

    @app.get("/api/status")
    async def status(request: Request):
        authorized(request)
        visual.expire()
        return {
            "settings": settings.public(),
            "sources": [asdict(s) for s in visual.sources.values()],
            "storage": visual.totals(),
            "history": visual.browse(),
            "operations": await asyncio.to_thread(operations.recent),
            "provider_limits": sorted(gate.limited),
            "usage": gate.usage[-20:],
            "costs": {
                "known_estimated_usd": gate.estimated_usd,
                "unknown_charges": gate.unknown_charges,
                "rate_date": str(gate.rates.date),
                "scope": "stored application estimates; partial provider coverage; recent usage is current process",
                "persistence": usage_storage.status(),
                "active_attempts": gate.active_usage(),
            },
            "resources": resource_monitor.snapshot(),
            **(await asyncio.to_thread(projects.snapshot)),
            "perception": {
                "enabled": settings.perception_enabled,
                "latest": perception["latest"],
                "error": perception["error"],
            },
            "events": list(behaviors.log),
            "thumbs": {
                "enabled": conversation().thumbs.enabled,
                "question": asdict(conversation().thumbs.question)
                if conversation().thumbs.question
                else None,
                "feedback": list(conversation().thumbs.feedback)[-5:],
            }
            if active["conversation"]
            else {"enabled": settings.thumb_responses_enabled, "question": None, "feedback": []},
        }

    @app.get("/api/behaviors")
    async def behavior_status(request: Request):
        authorized(request)
        return {"configuration": behavior_config.value.model_dump(), "events": list(behaviors.log)}

    @app.post("/api/behaviors/test")
    async def behavior_test(request: Request):
        authorized(request)
        data = bytearray()
        async for chunk in request.stream():
            if len(data) + len(chunk) > 2048:
                raise HTTPException(413, "Test event too large")
            data.extend(chunk)
        try:
            body = json.loads(data)
            rule = behaviors.rules.get(body["rule"])
            source = visual.sources.get(body["source"])
        except (ValueError, KeyError, TypeError):
            raise HTTPException(400, "Rule and camera source are required") from None
        if not rule or not source or not source.enabled or source.kind != "camera":
            raise HTTPException(400, "Choose an installed rule and active camera source")
        core = conversation()
        now = time.time()
        behavior_config.tick(now)
        behaviors.mode = core.mode
        event = Event(
            uuid.uuid4().hex,
            source.id,
            rule.event,
            now,
            1,
            duration=rule.persistence,
            generation=source.generation,
            detector="manual-test-event",
            details={"synthetic": True, "label": rule.label},
        )
        decision = behaviors.offer(event, now=now)
        return {"event": event.id, "decision": decision, "synthetic": True}

    @app.post("/api/behaviors")
    async def behavior_save(request: Request):
        authorized(request)
        from pydantic import ValidationError

        data = bytearray()
        async for chunk in request.stream():
            if len(data) + len(chunk) > 131072:
                raise HTTPException(413, "Behavior configuration too large")
            data.extend(chunk)
        try:
            behavior_config.save(json.loads(data))
        except (ValidationError, ValueError, TypeError):
            raise HTTPException(400, "Invalid behavior configuration") from None
        return {"saved": True}

    @app.post("/api/thumbs")
    async def thumb_settings(request: Request):
        authorized(request)
        body = await request.json()
        if not isinstance(body.get("enabled"), bool):
            raise HTTPException(400, "Enabled must be true or false")
        core = conversation()
        core.thumbs.enabled = body["enabled"]
        core.thumbs.invalidate("setting_changed")
        settings.thumb_responses_enabled = body["enabled"]
        return {"enabled": core.thumbs.enabled}

    @app.post("/api/projects")
    async def project_action(request: Request):
        authorized(request)
        body = await request.json()
        action, project = body.get("action"), body.get("project")
        if action == "add":
            if not isinstance(body.get("root"), str) or not isinstance(body.get("name"), str):
                raise HTTPException(400, "Project name and local folder are required")
            try:
                project = await asyncio.to_thread(projects.add_project, body["name"], body["root"])
            except OSError:
                raise HTTPException(400, "Folder is unavailable") from None
            schedule_index(project)
        elif action in {"activate", "remove"}:
            change = uuid.uuid4().hex
            project_changes.add(change)
            worker = None
            try:
                core = active["conversation"]
                if core:
                    await core.stop()
                    core.history.clear()
                    core.evidence.clear()
                    await core.emit("evidence", frames=[])
                worker = asyncio.create_task(
                    asyncio.to_thread(
                        projects.activate if action == "activate" else projects.remove, project
                    ),
                    name="project-change-" + change,
                )

                def finished(task):
                    project_changes.discard(change)
                    if not task.cancelled():
                        task.exception()  # Retrieve errors even after the HTTP caller disconnects.

                worker.add_done_callback(finished)
                await asyncio.shield(worker)
            finally:
                if worker is None:
                    project_changes.discard(change)
        elif action == "compact":
            await asyncio.to_thread(projects.compact)
        elif action == "refresh":
            await asyncio.to_thread(projects.status, project)
            schedule_index(project, force=True)
        elif action == "search":
            return {
                "passages": await asyncio.to_thread(projects.search, project, body.get("query", ""))
            }
        elif action == "read":
            return {
                "passages": await asyncio.to_thread(projects.read, project, body.get("ids", []))
            }
        else:
            raise HTTPException(400, "Unknown project action")
        return {"project": project, "active_project": projects.active}

    @app.get("/api/transcripts")
    async def list_transcripts(request: Request, offset: int = 0):
        authorized(request)
        state = await asyncio.to_thread(transcripts.state)
        rows = await asyncio.to_thread(transcripts.sessions, offset)
        return {
            **recorder.status(),
            **state,
            "sessions": rows,
            "next_offset": offset + len(rows) if len(rows) == 100 else None,
        }

    @app.post("/api/transcripts")
    async def transcript_action(request: Request):
        authorized(request)
        raw = bytearray()
        async for chunk in request.stream():
            if len(raw) + len(chunk) > 1024:
                raise HTTPException(413, "Transcript request too large")
            raw.extend(chunk)
        try:
            body = json.loads(raw)
            if not isinstance(body, dict):
                raise ValueError()
        except ValueError:
            raise HTTPException(400, "Invalid transcript request") from None
        if body.get("action") == "enabled" and type(body.get("enabled")) is bool:
            result = await recorder.change("enabled", body["enabled"])
            core = active["conversation"]
            if body["enabled"] and core and core.mode != "idle":
                core.record_session()
            return result
        if body.get("action") == "delete_all":
            return await recorder.change("delete", None)
        if body.get("action") == "delete" and isinstance(body.get("session"), str):
            try:
                session = uuid.UUID(hex=body["session"]).hex
            except ValueError:
                raise HTTPException(400, "Invalid session ID") from None
            return await recorder.change("delete", session)
        raise HTTPException(400, "Invalid transcript action")

    @app.get("/api/transcripts/{session}/export")
    async def export_transcript(session: str, request: Request):
        authorized(request)
        try:
            session = uuid.UUID(hex=session).hex
        except ValueError:
            raise HTTPException(400, "Invalid session ID") from None
        rows = await asyncio.to_thread(transcripts.entries, session, include_session=True)
        if not rows:
            raise HTTPException(404, "No saved transcript for this session")
        data = "\n\n".join(
            f"{row['role']} ({row['kind']}):\n{row['text']}"
            + (
                "\nMetadata: " + json.dumps(row["metadata"], sort_keys=True)
                if row["metadata"]
                else ""
            )
            for row in rows
        )
        return Response(
            data,
            media_type="text/plain; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="iago-transcript-{session}.txt"',
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @app.get("/api/notes")
    async def list_notes(request: Request, offset: int = 0):
        authorized(request)
        rows = await asyncio.to_thread(notes.list, offset)
        total = await asyncio.to_thread(notes.count)
        return {
            "notes": rows,
            "total": total,
            "next_offset": offset + len(rows) if offset + len(rows) < total else None,
        }

    @app.get("/api/notes/{note_id}/export")
    async def export_note(note_id: str, request: Request):
        authorized(request)
        try:
            normalized = uuid.UUID(hex=note_id).hex
        except ValueError:
            raise HTTPException(400, "Invalid note ID") from None
        data = await asyncio.to_thread(notes.export, normalized)
        return Response(
            data,
            media_type="text/plain; charset=utf-8",
            headers={
                "Content-Disposition": 'attachment; filename="iago-note-' + normalized + '.txt"',
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @app.get("/api/integrations")
    async def integration_status(request: Request):
        authorized(request)
        return await integrations.async_status()

    @app.get("/api/personality")
    async def personality_status(request: Request):
        authorized(request)
        return {"instructions": settings.personality, "default": DEFAULT_PERSONALITY}

    @app.get("/api/voice")
    async def voice_status(request: Request):
        authorized(request)
        core = active["conversation"]
        return {
            "selected": settings.tts_provider,
            "active": core.voice_provider if core else None,
            "reason": core.voice_reason if core else "not_started",
            "elevenlabs_voice_configured": bool(settings.elevenlabs_voice_id),
            "openai_voice": settings.openai_tts_voice,
            "speech_recovery": {name: voice.circuit.status() for name, voice in voices.items()},
        }

    @app.post("/api/voice")
    async def voice_control(request: Request):
        authorized(request)
        raw = bytearray()
        async for chunk in request.stream():
            if len(raw) + len(chunk) > 1024:
                raise HTTPException(413, "Voice selection too large")
            raw.extend(chunk)
        try:
            body = json.loads(raw)
            if not isinstance(body, dict) or set(body) != {"provider"}:
                raise ValueError()
        except ValueError:
            raise HTTPException(400, "Invalid voice selection") from None
        provider = voice_selection.validate(body["provider"])
        async with voice_selection_lock:
            await executor.journal(
                voice_selection.write,
                provider,
                observed=lambda saved: setattr(settings, "tts_provider", saved),
            )
        return {"selected": settings.tts_provider, "applies": "next_conversation_start"}

    @app.post("/api/personality")
    async def personality_control(request: Request):
        authorized(request)
        raw = bytearray()
        async for chunk in request.stream():
            if len(raw) + len(chunk) > 65536:
                raise HTTPException(413, "Personality request too large")
            raw.extend(chunk)
        try:
            body = json.loads(raw)
            if not isinstance(body, dict) or set(body) != {"instructions"}:
                raise ValueError()
        except ValueError:
            raise HTTPException(400, "Invalid personality request") from None
        value = personality.validate(body["instructions"])
        async with personality_lock:
            if active["conversation"]:
                await active["conversation"].stop()
            await executor.journal(
                personality.write,
                value,
                observed=lambda saved: setattr(settings, "personality", saved),
            )
        return {"instructions": settings.personality}

    def operation_status(record):
        tool = registry.tools.get(record["tool"])
        return {
            **{key: record[key] for key in ("id", "tool", "status", "updated")},
            "provider_cancel_available": bool(
                tool and tool.cancel_tool and record["status"] in {"dispatched", "uncertain"}
            ),
        }

    @app.get("/api/project-timings")
    async def project_timings(request: Request):
        authorized(request)
        return projects.timings.snapshot()

    @app.get("/api/resources")
    async def resource_history(request: Request):
        authorized(request)
        return resource_monitor.snapshot(history=True)

    @app.post("/api/operations/cancel")
    async def provider_cancel_control(request: Request):
        authorized(request)
        raw = bytearray()
        async for chunk in request.stream():
            if len(raw) + len(chunk) > 1024:
                raise HTTPException(413, "Cancellation request too large")
            raw.extend(chunk)
        try:
            body = json.loads(raw)
            if (
                not isinstance(body, dict)
                or set(body) not in ({"operation_id"}, {"operation_id", "request_id", "binding"})
                or not all(
                    isinstance(value, str) and 0 < len(value) <= 128 for value in body.values()
                )
            ):
                raise ValueError()
        except ValueError:
            raise HTTPException(400, "Invalid cancellation request") from None
        target = body["operation_id"]
        if "request_id" in body:
            proposal = executor.pending.get(body["request_id"])
            if not proposal or proposal["payload"] != {"operation_id": target}:
                raise ToolError("invalid_confirmation")
            context = proposal["context"]
            executor.confirm(body["request_id"], body["binding"], input_kind="action_ui")
        else:
            context = CallContext(
                "operation-control",
                0,
                capabilities=frozenset(integrations.capabilities),
                operation_id=uuid.uuid4().hex,
            )
        result = await executor.request_cancellation(target, context)
        if result["status"] == "confirmation_required":
            proposal = await executor.request_cancellation(target, context, prepare=True)
            return {"confirmation": proposal}
        return {
            "request": result,
            "original_outcome": "Check the original operation separately; a cancellation receipt does not prove it was undone.",
        }

    @app.get("/api/operations")
    async def operation_list(request: Request):
        authorized(request)
        return {
            "operations": [
                operation_status(row) for row in await executor.journal(operations.recent)
            ],
            "storage": await executor.journal(operations.storage_status),
        }

    @app.post("/api/operations")
    async def operation_control(request: Request):
        authorized(request)
        raw = bytearray()
        async for chunk in request.stream():
            if len(raw) + len(chunk) > 1024:
                raise HTTPException(413, "Operation request too large")
            raw.extend(chunk)
        try:
            body = json.loads(raw)
            if (
                not isinstance(body, dict)
                or set(body) != {"action", "operation_id"}
                or not isinstance(body["operation_id"], str)
                or not 0 < len(body["operation_id"]) <= 128
            ):
                raise ValueError()
        except ValueError:
            raise HTTPException(400, "Invalid operation request") from None
        if body["action"] == "cancel_undispatched":
            result = await executor.cancel_undispatched(body["operation_id"])
            return {
                "operation": operation_status(result["operation"]),
                "canceled": result["canceled"],
            }
        if body["action"] == "reconcile":
            context = CallContext(
                "operation-control",
                0,
                capabilities=frozenset(integrations.capabilities),
                operation_id=body["operation_id"],
            )
            result = await executor.reconcile(body["operation_id"], context)
            return {"operation": operation_status(result)}
        raise HTTPException(400, "Unknown operation action")

    @app.post("/api/integrations")
    async def integration_control(request: Request):
        authorized(request)
        raw = bytearray()
        async for chunk in request.stream():
            if len(raw) + len(chunk) > 2048:
                raise HTTPException(413, "Integration setting too large")
            raw.extend(chunk)
        try:
            body = json.loads(raw)
            if not isinstance(body, dict):
                raise ValueError()
        except ValueError:
            raise HTTPException(400, "Invalid integration setting") from None
        if body.get("action") in {"tool_enabled", "tool_policy"}:
            tool = registry.tools.get(body.get("tool"))
            if not tool:
                raise HTTPException(400, "Unknown tool or invalid setting")
            enable_change = body["action"] == "tool_enabled"
            if (enable_change and type(body.get("enabled")) is not bool) or (
                not enable_change and body.get("policy") not in ("allow", "confirm", "deny")
            ):
                raise HTTPException(400, "Invalid tool setting")
            async with tool_preferences_lock:
                values = tool_preferences.replacement(
                    tool.key,
                    enabled=body["enabled"] if enable_change else None,
                    mode=None if enable_change else body["policy"],
                )

                def applied(saved):
                    tool_preferences.apply(saved)
                    for operation in list(executor.pending):
                        executor.drop_proposal(operation)

                await executor.journal(tool_preferences.write, values, observed=applied)
        elif body.get("action") == "module_enabled":
            if (
                not isinstance(body.get("module"), str)
                or not isinstance(body.get("account"), str)
                or type(body.get("enabled")) is not bool
            ):
                raise HTTPException(400, "Invalid module setting")
            async with tool_preferences_lock:

                def applied_modules(installed):
                    integrations.configured_modules = installed
                    if not body["enabled"]:
                        connection = registry.connections.get((body["module"], body["account"]))
                        if connection:
                            connection.disconnect()
                    integrations.refresh_capabilities()
                    for operation in list(executor.pending):
                        executor.drop_proposal(operation)

                await executor.journal(
                    integrations.save_module_enabled,
                    body["module"],
                    body["account"],
                    body["enabled"],
                    observed=applied_modules,
                )
        elif body.get("action") == "skill_enabled":
            if not isinstance(body.get("skill"), str) or type(body.get("enabled")) is not bool:
                raise HTTPException(400, "Invalid workflow setting")
            async with tool_preferences_lock:

                def applied_skills(installed):
                    integrations.skills.installed = installed
                    integrations.skills.entries.clear()
                    integrations.skill_revision += 1
                    for operation in list(executor.pending):
                        executor.drop_proposal(operation)

                await executor.journal(
                    integrations.save_skill_enabled,
                    body["skill"],
                    body["enabled"],
                    observed=applied_skills,
                )
        elif body.get("action") == "disconnect":
            if not isinstance(body.get("module"), str) or not isinstance(body.get("account"), str):
                raise HTTPException(400, "Invalid connection")
            connection = registry.connections.get((body.get("module"), body.get("account")))
            if not connection:
                raise HTTPException(400, "Unknown connection")
            connection.disconnect()
            integrations.refresh_capabilities()
            for operation in list(executor.pending):
                executor.drop_proposal(operation)
            if active["conversation"]:
                await active["conversation"].stop()
            await integrations.disconnect_credentials(body["module"], body["account"])
            if any(
                (item.get("module"), item.get("account")) == (body["module"], body["account"])
                for item in integrations.configured_modules
            ):
                async with tool_preferences_lock:
                    await executor.journal(
                        integrations.save_module_enabled,
                        body["module"],
                        body["account"],
                        False,
                        observed=lambda installed: setattr(
                            integrations, "configured_modules", installed
                        ),
                    )
        else:
            raise HTTPException(400, "Unknown integration setting")
        if active["conversation"]:
            await active["conversation"].stop()
        return await integrations.async_status()

    @app.post("/api/notes")
    async def note_action(request: Request):
        authorized(request)
        raw = bytearray()
        async for chunk in request.stream():
            if len(raw) + len(chunk) > 196608:
                raise HTTPException(413, "Note request too large")
            raw.extend(chunk)
        try:
            body = json.loads(raw)
            if not isinstance(body, dict):
                raise ValueError()
        except ValueError:
            raise HTTPException(400, "Invalid note request") from None
        if (
            body.get("action") == "save"
            and isinstance(body.get("text"), str)
            and 0 < len(body["text"]) <= 12000
        ):
            return {"id": await asyncio.to_thread(notes.save, body["text"])}
        if body.get("action") == "delete":
            await asyncio.to_thread(notes.delete, body.get("id", ""))
            return {"status": "deleted"}
        raise HTTPException(400, "Invalid note action")

    @app.post("/api/source")
    async def source(request: Request):
        authorized(request)
        body = await request.json()
        core = conversation()
        if core.mode == "idle":
            raise HTTPException(409, "Enter Aware or Conversation before capture")
        return asdict(
            visual.source(
                page_owner or core.connection, body["kind"], body.get("label", body["kind"])
            )
        )

    @app.post("/api/frame/{source_id}/{generation}")
    async def frame(source_id: str, generation: int, request: Request):
        authorized(request)
        if int(request.headers.get("content-length", "0")) > 20 * 1024 * 1024:
            raise HTTPException(413, "Image too large")
        data = bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data) > 20 * 1024 * 1024:
                raise HTTPException(413, "Image too large")
        prepared = await asyncio.to_thread(visual.prepare, bytes(data))
        captured = float(request.headers.get("x-captured-at", "0"))
        result = visual.add(source_id, generation, captured, prepared)
        return visual.describe(result)

    @app.post("/api/perception/{source_id}/{generation}")
    async def detector_frame(source_id: str, generation: int, request: Request):
        authorized(request)
        source = visual.sources.get(source_id)
        if not settings.perception_enabled:
            raise HTTPException(409, "Local perception is disabled in configuration")
        if (
            not source
            or not source.enabled
            or source.kind != "camera"
            or source.generation != generation
            or conversation().mode == "idle"
        ):
            raise HTTPException(409, "Ineligible or stale live camera")
        if perception["source"] not in {None, source_id}:
            previous = visual.sources.get(perception["source"])
            if previous and previous.enabled:
                raise HTTPException(409, "Another live camera owns perception")
        data = bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data) > 1024 * 1024:
                raise HTTPException(413, "Detector frame too large")
        if not perception["worker"]:
            perception["worker"] = PerceptionWorker(settings.perception_models)
        perception["source"] = source_id
        perception["worker"].offer(
            source_id, generation, float(request.headers.get("x-captured-at", "0")), bytes(data)
        )
        return {"status": "queued"}

    @app.get("/api/frame/{frame_id}")
    async def image(frame_id: str, request: Request, thumbnail: bool = False):
        authorized(request)
        frame = visual.get(frame_id)
        return Response(
            frame.thumbnail if thumbnail else frame.image,
            media_type="image/jpeg",
            headers={"Cache-Control": "no-store"},
        )

    @app.post("/api/visual")
    async def visual_action(request: Request):
        authorized(request)
        body = await request.json()
        core = conversation()
        if body["action"] == "pin":
            visual.pin(body["frame"], body.get("label", "Reference"))
        elif body["action"] == "unpin":
            visual.unpin(body["frame"])
        elif body["action"] in {"clear", "off"}:
            await core.clear_evidence(body.get("source"), disable=body["action"] == "off")
        else:
            raise HTTPException(400, "Unknown visual action")
        return {"sources": [asdict(s) for s in visual.sources.values()]}

    async def authenticate_ws(ws):
        origin = ws.headers.get("origin", "")
        if origin not in {
            f"http://127.0.0.1:{settings.desktop_port}",
            f"http://localhost:{settings.desktop_port}",
        }:
            await ws.close(code=1008)
            return False
        await ws.accept()
        async with asyncio.timeout(5):
            supplied = await ws.receive_text()
        if not secrets.compare_digest(supplied, token):
            await ws.close(code=1008)
            return False
        return True

    @app.websocket("/control")
    async def control(ws: WebSocket):
        nonlocal page_owner
        if not await authenticate_ws(ws):
            return
        if active["control"]:
            await ws.send_json(
                {"type": "error", "message": "Another page owns audio. End it first."}
            )
            await ws.close(code=1008)
            return
        active["control"] = ws
        page_owner = secrets.token_hex(16)

        async def send(message):
            if active["control"] is ws:
                try:
                    async with asyncio.timeout(2):
                        await ws.send_json(message)
                except (
                    TimeoutError,
                    RuntimeError,
                    WebSocketDisconnect,
                    BrokenResourceError,
                    ClosedResourceError,
                ):
                    pass

        core = (
            active["conversation"]
            if robot_mode
            else Conversation(settings, brain, voices, executor, visual, send)
        )
        core.project_context = lambda: projects.active
        core.project_context_ready = lambda: not project_changes
        core.project_context_generation = lambda: projects.generation
        core.record = recorder.submit
        core.record_snapshot = recorder.snapshot
        core.runtime_capabilities = lambda: frozenset(integrations.capabilities)
        active["conversation"] = core
        await core.emit(
            "ready",
            mode=core.mode,
            deployment=settings.deployment_mode,
            audio_settings=robot_session.audio_settings if robot_session else None,
            robot_camera_enabled=robot_session.camera_enabled if robot_session else None,
            robot_motion_enabled=getattr(robot_session, "motion_enabled", False),
        )
        if robot_mode:
            await core.emit(
                "state", mode=core.mode, voice=core.voice_provider, voice_reason=core.voice_reason
            )
        try:
            while True:
                raw = await ws.receive_text()
                if len(raw) > 20000:
                    raise ToolError("control_message_limit")
                message = json.loads(raw)
                kind = message.get("type")
                try:
                    if kind == "heartbeat":
                        await core.emit("heartbeat")
                    elif kind == "stop":
                        if trigger_window.starting:
                            await trigger_window.cancel()
                        await core.stop(generation=int(message.get("generation", 0)))
                    elif kind == "speech_start" and not robot_mode:
                        captured = float(message.get("captured", 0))
                        if 0 <= time.time() - captured <= 2:
                            await core.speech_onset(captured)
                    elif kind == "robot_reconnect" and robot_session:
                        robot_session.reconnect()
                    elif kind == "robot_motion" and robot_session:
                        await robot_session.set_motion(message["enabled"])
                    elif kind == "robot_cue" and robot_session:
                        await robot_session.cue("acknowledge", core.epoch)
                    elif kind == "robot_camera" and robot_session:
                        await robot_session.set_camera(message["enabled"])
                    elif kind == "audio_settings" and robot_session:
                        await robot_session.configure_audio(
                            message["muted"], message["patient"], message["volume"]
                        )
                    elif kind == "mode":
                        await trigger_window.cancel()
                        await change_mode(core, message["mode"])
                    elif kind == "user":
                        core.selected_source = message.get("source") or None
                        await core.user_turn(message.get("text", ""))
                    elif kind == "voice_preview":
                        await core.preview_voice()
                    elif kind == "source":
                        core.selected_source = message.get("source") or None
                    elif kind == "confirmation":
                        operation = message.get("operation_id")
                        future = core.confirmations.get(operation)
                        if future is None or future.done():
                            raise ToolError("stale_confirmation")
                        approved = message.get("approved") is True
                        if approved:
                            executor.confirm(
                                operation, message.get("binding"), input_kind="action_ui"
                            )
                        future.set_result(approved)
                    elif kind == "commit" and robot_session:
                        await robot_session.finish_turn()
                    elif kind == "heard" and not robot_mode:
                        await core.heard(int(message["epoch"]), message["segment"])
                    elif kind == "played" and core.speech and not robot_mode:
                        core.speech.acknowledge(int(message["epoch"]), int(message["sequence"]))
                except Exception as exc:
                    await core.emit(
                        "error",
                        message=f"Action unavailable ({getattr(exc, 'code', type(exc).__name__)}).",
                    )
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            # ASGI disconnect cancellation must not strand control ownership while
            # source clearing performs bounded edge stop/notification work.
            with CancelScope(shield=True):
                try:
                    if robot_mode:
                        if any(
                            s.owner == page_owner and s.enabled for s in visual.sources.values()
                        ):
                            await core.clear_evidence(disable=True, owner=page_owner)
                    else:
                        try:
                            await trigger_window.cancel()
                            await core.set_mode("idle")
                        finally:
                            try:
                                await close_stt()
                            finally:
                                active["conversation"] = None
                finally:
                    active["control"] = None
                    page_owner = None

    @app.websocket("/audio")
    async def audio(ws: WebSocket):
        if robot_mode:
            await ws.close(code=1008)
            return
        if not await authenticate_ws(ws):
            return
        if active["audio"]:
            await ws.close(code=1008)
            return
        active["audio"] = ws
        try:
            await ws.send_json({"type": "audio_ready"})
            while True:
                packet = await ws.receive()
                if packet["type"] == "websocket.disconnect":
                    break
                pcm = packet.get("bytes")
                timing = {}
                if pcm is None:
                    raw = packet.get("text", "")
                    try:
                        marker = json.loads(raw) if len(raw) <= 256 else None
                        if (
                            not isinstance(marker, dict)
                            or marker.get("type") != "commit"
                            or set(marker) - {"type", "capture_start", "capture_end"}
                        ):
                            raise ValueError()
                        if set(marker) != {"type"}:
                            start, end = marker.get("capture_start"), marker.get("capture_end")
                            if (
                                not all(
                                    type(v) in (int, float) and math.isfinite(v)
                                    for v in (start, end)
                                )
                                or not 0 <= start <= end
                                or end - start > 3600
                                or not -0.2 <= time.time() - end <= 2
                            ):
                                raise ValueError()
                            timing = {"capture_start": start, "capture_end": end}
                    except ValueError:
                        raise ToolError("invalid_microphone_marker") from None
                if (
                    active["stt"]
                    and active["conversation"]
                    and active["conversation"].mode == "conversation"
                ):
                    async with asyncio.timeout(0.25):
                        if pcm is None:
                            await active["conversation"].commit_recognition(active["stt"], **timing)
                        else:
                            await active["stt"].append(pcm)
        except (WebSocketDisconnect, TimeoutError, RuntimeError, ToolError):
            if active["conversation"]:
                await active["conversation"].stop()
        finally:
            if active["audio"] is ws:
                active["audio"] = None
                await trigger_window.cancel()
                await close_stt()
                core = active["conversation"]
                if core and core.mode == "conversation":
                    await core.set_mode("aware")

    return app
