"""`QuietnessBudget` — global cap on moderator utterances (brief §7.4).

This is the strongest expression of the "bias toward silence" product
principle. Live-adjustable mid-session via the `set_quietness_budget`
researcher command (brief §5.4).
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, NonNegativeFloat, PositiveInt


class QuietnessBudget(BaseModel):
    """Per-session rate limit on moderator speech."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    max_utterances_per_10min: PositiveInt = Field(
        default=3,
        description="Hard cap on moderator utterances within the rolling 10-minute window.",
    )
    min_seconds_between_utterances: Annotated[NonNegativeFloat, Field(ge=0.0)] = Field(
        default=30.0,
        description="Floor enforced regardless of remaining window capacity.",
    )
    current_window_count: Annotated[int, Field(ge=0)] = Field(
        default=0,
        description="Utterances spoken inside the active rolling window.",
    )
    last_utterance_at: datetime | None = None
