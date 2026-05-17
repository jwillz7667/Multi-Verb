"""`pick_voice_for_persona` — best-fit suggestion from the curated library.

Researchers always have the last word in the persona editor (P4 L10),
but a new study starts unbiased: this picker proposes the voice whose
tags align most closely with the persona's `formality` + `tone`, so
researchers don't have to read seven blurbs to start.

Scoring is intentionally simple — formality match weighs more than
warmth because formality is the persona attribute that mismatches
most jarringly in audio (a casual study with a formal voice reads as
'tone-deaf'; a warm vs. neutral voice differs more subtly).

Returns a `CuratedVoice`. Raises `ValueError` only if the library has
no voices for the persona's `voice_provider` — an invariant of the
library, surfaced loudly rather than swallowed.
"""

from __future__ import annotations

from verbio_engine.mouth.persona import ModeratorPersona, PersonaTone
from verbio_engine.voices.library import (
    VOICE_LIBRARY,
    CuratedVoice,
    VoiceWarmth,
    list_voices_for_provider,
)

_FORMALITY_WEIGHT = 3
_WARMTH_WEIGHT = 2


def _tone_to_warmth(tone: PersonaTone) -> VoiceWarmth:
    if tone == "warm":
        return "warm"
    if tone == "professional":
        return "cool"
    return "neutral"


def _score(voice: CuratedVoice, persona: ModeratorPersona) -> int:
    score = 0
    if voice.formality == persona.formality:
        score += _FORMALITY_WEIGHT
    if voice.warmth == _tone_to_warmth(persona.tone):
        score += _WARMTH_WEIGHT
    return score


def pick_voice_for_persona(
    persona: ModeratorPersona,
    *,
    library: tuple[CuratedVoice, ...] = VOICE_LIBRARY,
) -> CuratedVoice:
    """Highest-scoring voice for `persona`; ties broken by library order."""
    candidates = list_voices_for_provider(persona.voice_provider, library=library)
    if not candidates:
        msg = (
            f"curated voice library has no voices for provider={persona.voice_provider!r} — "
            "every provider must ship at least one voice"
        )
        raise ValueError(msg)

    # max() with a key preserves the first-best on ties (Python's stable
    # ordering). Library order is the documented tiebreaker.
    return max(candidates, key=lambda v: _score(v, persona))
