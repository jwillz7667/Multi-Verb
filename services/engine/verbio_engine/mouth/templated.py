"""`TemplatedMouth` — the §8.4 fallback path as a first-class `MouthClient`.

Always emits the pre-written phrasing from `format_template`, in one
chunk, marked `from_fallback=True`. Two callers:

  - Shadow-mode + smoke tests where we want the moderator pipeline
    end-to-end without billing the LLM.
  - `DeepSeekMouth`'s own fallback path delegates here when the LLM
    times out or errors — keeps the §8.4 phrasings in one place rather
    than duplicated in DeepSeekMouth's except branch.

Pure, synchronous under the hood (templates are dict lookups); the
async signature exists only to satisfy the `MouthClient` Protocol.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import cast

from verbio_engine.domain.decision import DecisionAction
from verbio_engine.mouth.protocol import MouthChunk, MouthRequest
from verbio_engine.mouth.templates import format_template


class TemplatedMouth:
    """Mouth implementation that always emits the §8.4 fallback phrasing."""

    async def phrase(self, request: MouthRequest) -> AsyncIterator[MouthChunk]:
        # `MouthRequest.action` is typed as `str` to avoid an import
        # cycle through the protocol module; the orchestrator only
        # constructs requests from real `DecisionAction` values.
        text = format_template(cast("DecisionAction", request.action), request.context)
        yield MouthChunk(text=text, is_final=True, from_fallback=True)
