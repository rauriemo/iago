"""Dated returned-token/duration estimates; absent usage/rates never means zero cost."""

import math
from datetime import date
from decimal import Decimal
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class TokenRates(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    input: Decimal = Field(ge=0)
    cached: Decimal = Field(ge=0)
    cache_write: Decimal = Field(ge=0)
    output: Decimal = Field(ge=0)
    long_threshold: int = Field(gt=0)
    long_input_multiplier: Decimal = Field(ge=1)
    long_output_multiplier: Decimal = Field(ge=1)


class DurationRates(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    usd_per_minute: Decimal = Field(ge=0)
    source: str = Field(min_length=1, max_length=512)


class RateTable(BaseModel):
    model_config = ConfigDict(extra="forbid")
    date: date
    source: str = Field(min_length=1, max_length=512)
    models: dict[str, TokenRates] = Field(max_length=20)
    duration_models: dict[str, DurationRates] = Field(default_factory=dict, max_length=20)

    @classmethod
    def load(cls, path=None):
        path = Path(path) if path else Path(__file__).with_name("rates.json")
        if path.stat().st_size > 65536:
            raise ValueError("cost_rate_table_limit")
        return cls.model_validate_json(path.read_text(encoding="utf-8"))

    def estimate(self, model, usage, *, service_tier="default"):
        if service_tier != "default" or not isinstance(usage, dict):
            return None
        if model in self.duration_models:
            seconds = usage.get("returned_duration_seconds")
            count = usage.get("duration_usage_items")
            if (
                type(seconds) not in (int, float)
                or not 0 <= seconds <= 10**9
                or not math.isfinite(seconds)
                or type(count) is not int
                or not 1 <= count <= 10000
            ):
                return None
            return Decimal(str(seconds)) * self.duration_models[model].usd_per_minute / Decimal(60)
        rates = self.models.get(model)
        if not rates:
            return None
        details = usage.get("input_tokens_details")
        if not isinstance(details, dict):
            return None
        values = (
            usage.get("input_tokens"),
            details.get("cached_tokens"),
            details.get("cache_write_tokens", 0),
            usage.get("output_tokens"),
        )
        if not all(type(value) is int and 0 <= value <= 10**9 for value in values):
            return None
        total, cached, written, output = values
        if cached + written > total:
            return None
        input_cost = (
            (total - cached - written) * rates.input
            + cached * rates.cached
            + written * rates.cache_write
        )
        output_cost = output * rates.output
        if total > rates.long_threshold:
            input_cost *= rates.long_input_multiplier
            output_cost *= rates.long_output_multiplier
        return (input_cost + output_cost) / Decimal(1000000)
