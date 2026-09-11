"""One temporary trigger-started conversation; manual control cancels its owner task."""

import asyncio
import contextlib


class TriggerWindow:
    def __init__(self):
        self.task = None
        self.starting = False

    @property
    def busy(self):
        return self.task is not None and not self.task.done()

    def start(self, core, intent, valid, change_mode, seconds):
        if self.busy:
            return False
        self.starting = True
        self.task = asyncio.create_task(self.run(core, intent, valid, change_mode, seconds))
        return True

    async def run(self, core, intent, valid, change_mode, seconds):
        revision = core.user_revision
        activated = False
        try:
            if not valid() or core.mode != "aware":
                return
            await change_mode(core, "conversation")
            activated = True
            self.starting = False
            if not valid():
                return
            if not await core.proactive(intent, valid):
                return
            deadline = asyncio.get_running_loop().time() + seconds
            while core.mode == "conversation" and core.user_revision == revision:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    break
                await asyncio.sleep(min(0.1, remaining))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await core.emit(
                "error", message="Triggered conversation unavailable (" + type(exc).__name__ + ")."
            )
        finally:
            self.starting = False
            if activated and core.mode == "conversation" and core.user_revision == revision:
                await change_mode(core, "aware")

    async def cancel(self):
        if self.busy:
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.task
