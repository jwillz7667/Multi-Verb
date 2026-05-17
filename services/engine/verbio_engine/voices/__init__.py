"""`verbio_engine.voices` — curated voice library + persona-aware picker.

Brief §9 mandates a curated list of 6-8 voices per TTS provider, each
pre-tagged with persona attributes (formality, warmth, pace). The
dashboard's persona editor (P4 L10) lists these voices in a dropdown
so researchers never paste raw provider voice ids — that keeps voice
selection grounded in attributes the moderator's behaviour actually
cares about.

Public surface:
  - `CuratedVoice` — provider id + display name + persona tags.
  - `VoiceFormality` / `VoiceWarmth` / `VoicePace` — closed-enum tags.
  - `VOICE_LIBRARY` — the canonical curated set (frozen).
  - `list_voices_for_provider(provider)` — UI-facing filter.
  - `get_voice(voice_id, provider)` — lookup by id, raises if absent.
  - `pick_voice_for_persona(persona)` — best-fit suggestion given a
    persona's `voice_provider`/`tone`/`formality`.
"""

from verbio_engine.voices.library import (
    VOICE_LIBRARY,
    CuratedVoice,
    UnknownVoiceError,
    VoiceFormality,
    VoicePace,
    VoiceWarmth,
    get_voice,
    list_voices_for_provider,
)
from verbio_engine.voices.picker import pick_voice_for_persona

__all__ = [
    "VOICE_LIBRARY",
    "CuratedVoice",
    "UnknownVoiceError",
    "VoiceFormality",
    "VoicePace",
    "VoiceWarmth",
    "get_voice",
    "list_voices_for_provider",
    "pick_voice_for_persona",
]
