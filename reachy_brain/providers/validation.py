"""Live validation shares the application's accounting and exclusive ownership."""

from contextlib import asynccontextmanager

from .live import ProviderGate
from .usage_storage import UsageStorage


@asynccontextmanager
async def validation_gate(settings):
    gate = ProviderGate(settings)
    storage = UsageStorage(gate, settings.data_dir / "usage-accounting.json")
    try:
        await storage.start()
        yield gate
    finally:
        await storage.close()
