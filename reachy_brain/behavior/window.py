"""One temporary trigger-started conversation; manual control cancels its owner task."""

import asyncio
import contextlib


class TriggerWindow:
    def __init__(self, observe=None):
        self.task = None
        self.starting = False
        self.error = None
        self.observe = observe or (lambda intent, decision: None)

    @property
    def busy(self):
        return self.task is not None and not self.task.done()

    def start(self, core, intent, valid, change_mode, seconds):
        if self.busy:
            return False
        self.error = None
        self.starting = True
        self.task = asyncio.create_task(self.run(core, intent, valid, change_mode, seconds))
        return True

    async def report_error(self, core, exc):
        self.error = type(exc).__name__
        # A closed UI transport cannot turn an observed lifecycle failure into
        # another unhandled background exception. Keep the diagnostic available.
        with contextlib.suppress(Exception):
            await core.emit(
                "error", message="Triggered conversation transition failed (" + self.error + ")."
            )

    async def run(self, core, intent, valid, change_mode, seconds):
        revision = core.user_revision
        activated = False
        failures = []
        outcome = "window_invalidated"
        try:
            if not valid() or core.mode != "aware":
                return
            self.observe(intent, "window_starting")
            await change_mode(core, "conversation")
            activated = True
            self.starting = False
            if not valid():
                return
            if not await core.proactive(intent, valid):
                outcome = "greeting_declined"
                return
            self.observe(intent, "greeting_scheduled")
            deadline = asyncio.get_running_loop().time() + seconds
            while core.mode == "conversation" and core.user_revision == revision:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    break
                await asyncio.sleep(min(0.1, remaining))
            outcome = (
                "window_promoted"
                if core.user_revision != revision
                else "window_timed_out"
                if core.mode == "conversation"
                else "window_mode_changed"
            )
        except asyncio.CancelledError:
            outcome = "window_canceled"
            raise
        except Exception as exc:
            outcome = "window_failed"
            failures.append(exc)
        finally:
            self.starting = False
            if activated and core.mode == "conversation" and core.user_revision == revision:
                try:
                    await change_mode(core, "aware")
                except Exception as exc:
                    failures.append(exc)
                    if core.mode == "conversation" and core.user_revision == revision:
                        try:
                            await change_mode(core, "idle")
                        except Exception as fallback:
                            failures.append(fallback)
            self.observe(intent, outcome)
            if failures:
                self.observe(intent, "window_transition_failed")
            # Complete owned mode cleanup before awaiting potentially slow UI delivery.
            for failure in failures:
                await self.report_error(core, failure)

    async def cancel(self):
        if self.busy:
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.task
