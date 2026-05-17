"""`CuratedVoice` and the per-provider voice library (brief §9).

Each `CuratedVoice` carries a provider id, a researcher-facing display
name, and the three persona attributes the picker scores on
(formality, warmth, pace). The library ships with seven voices per
provider — well inside the brief's 6-8 range — chosen from each
provider's published default catalog so the engine works out of the
box. Orgs can override or extend by replacing this module; the picker
takes an explicit `library` argument for that reason.

Voice ids are externally-owned strings; we treat them as opaque. If a
provider rotates an id, the `test_default_library_is_internally_consistent`
test still passes (it pins structure, not specific ids), but
`get_voice` will surface a clear `UnknownVoiceError` at runtime if a
persona references a now-deleted voice.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from verbio_engine.mouth.persona import VoiceProvider

VoiceFormality = Literal["casual", "neutral", "formal"]
"""Aligned with `PersonaFormality` so picker matching is a direct comparison."""

VoiceWarmth = Literal["warm", "neutral", "cool"]
"""How affectively warm the voice reads; `PersonaTone` maps onto this."""

VoicePace = Literal["brisk", "steady", "measured"]
"""Natural speaking rate; surfaced for UI display, not currently scored."""


class UnknownVoiceError(LookupError):
    """`voice_id` not present in the curated library for `provider`.

    Raised by `get_voice` when a persona references an id the library
    no longer carries — usually because the provider rotated their
    default catalog or the org's curation was edited out from under a
    live persona.
    """


class CuratedVoice(BaseModel):
    """One entry in the curated voice library.

    Frozen + extra-forbid so a typo'd tag key surfaces at module
    import, not silently as a missing attribute downstream.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    voice_id: str = Field(..., min_length=1)
    provider: VoiceProvider
    display_name: str = Field(..., min_length=1, max_length=80)
    formality: VoiceFormality
    warmth: VoiceWarmth
    pace: VoicePace
    description: str = Field(..., min_length=1, max_length=240)


# ---------------------------------------------------------------------------
# Cartesia Sonic curation
# ---------------------------------------------------------------------------
# Voice ids are from Cartesia's published default voice library. Display
# names are paraphrased so the dashboard doesn't expose internal codenames.

_CARTESIA_VOICES: tuple[CuratedVoice, ...] = (
    CuratedVoice(
        voice_id="79a125e8-cd45-4c13-8a67-188112f4dd22",
        provider="cartesia",
        display_name="Bridget",
        formality="formal",
        warmth="neutral",
        pace="measured",
        description="Crisp British delivery; reads steady and authoritative.",
    ),
    CuratedVoice(
        voice_id="156fb8d2-335b-4950-9cb3-a2d33befec77",
        provider="cartesia",
        display_name="Margot",
        formality="neutral",
        warmth="warm",
        pace="steady",
        description="Helpful, approachable; default fit for most studies.",
    ),
    CuratedVoice(
        voice_id="2deb3edf-b9d8-4d06-8db9-5742fb8a3cb2",
        provider="cartesia",
        display_name="Hazel",
        formality="casual",
        warmth="warm",
        pace="steady",
        description="Soft and conversational; lowers tension in tense groups.",
    ),
    CuratedVoice(
        voice_id="d46abd1d-2d02-43e8-819f-51fb652c1c61",
        provider="cartesia",
        display_name="Daniel",
        formality="formal",
        warmth="cool",
        pace="measured",
        description="News-reader cadence; reads as neutral and impartial.",
    ),
    CuratedVoice(
        voice_id="694f9389-aac1-45b6-b726-9d9369183238",
        provider="cartesia",
        display_name="Sarah",
        formality="neutral",
        warmth="warm",
        pace="brisk",
        description="Quick, engaged delivery; good for short interventions.",
    ),
    CuratedVoice(
        voice_id="c8605446-247c-4d39-acd4-8f4c28aa363c",
        provider="cartesia",
        display_name="Eleanor",
        formality="formal",
        warmth="warm",
        pace="measured",
        description="Considered and unhurried; suits sensitive subject matter.",
    ),
    CuratedVoice(
        voice_id="69267136-1bdc-412f-ad78-0caad210fb40",
        provider="cartesia",
        display_name="Owen",
        formality="casual",
        warmth="neutral",
        pace="steady",
        description="Easygoing male voice; for sessions that need to feel informal.",
    ),
)


# ---------------------------------------------------------------------------
# ElevenLabs Flash curation
# ---------------------------------------------------------------------------
# Voice ids are from ElevenLabs' default starter library — stable since
# the Flash v2.5 model line shipped.

_ELEVENLABS_VOICES: tuple[CuratedVoice, ...] = (
    CuratedVoice(
        voice_id="21m00Tcm4TlvDq8ikWAM",
        provider="elevenlabs",
        display_name="Rachel",
        formality="neutral",
        warmth="warm",
        pace="steady",
        description="Calm female voice; widely-tested default for general use.",
    ),
    CuratedVoice(
        voice_id="EXAVITQu4vr4xnSDxMAC",
        provider="elevenlabs",
        display_name="Sarah",
        formality="neutral",
        warmth="warm",
        pace="brisk",
        description="Soft, friendly delivery; good for engaging quiet participants.",
    ),
    CuratedVoice(
        voice_id="pNInz6obpgDQGcFmaJgB",
        provider="elevenlabs",
        display_name="Adam",
        formality="formal",
        warmth="cool",
        pace="measured",
        description="Deep, professional male voice; reads as impartial.",
    ),
    CuratedVoice(
        voice_id="AZnzlk1XvdvUeBnXmlld",
        provider="elevenlabs",
        display_name="Domi",
        formality="neutral",
        warmth="neutral",
        pace="brisk",
        description="Confident female voice; suits redirect/pressure interventions.",
    ),
    CuratedVoice(
        voice_id="TxGEqnHWrfWFTfGW9XjX",
        provider="elevenlabs",
        display_name="Josh",
        formality="casual",
        warmth="warm",
        pace="steady",
        description="Warm conversational male voice; lowers formality in groups.",
    ),
    CuratedVoice(
        voice_id="MF3mGyEYCl7XYWbV9V6O",
        provider="elevenlabs",
        display_name="Elli",
        formality="casual",
        warmth="warm",
        pace="brisk",
        description="Younger female voice; suits casual or peer-style studies.",
    ),
    CuratedVoice(
        voice_id="VR6AewLTigWG4xSOukaG",
        provider="elevenlabs",
        display_name="Arnold",
        formality="formal",
        warmth="cool",
        pace="measured",
        description="Crisp male voice; reads as composed and unhurried.",
    ),
)


VOICE_LIBRARY: tuple[CuratedVoice, ...] = _CARTESIA_VOICES + _ELEVENLABS_VOICES
"""Canonical curated voice set. Replace this constant to ship org curation."""


def list_voices_for_provider(
    provider: VoiceProvider,
    *,
    library: tuple[CuratedVoice, ...] = VOICE_LIBRARY,
) -> tuple[CuratedVoice, ...]:
    """Voices for one provider in their library declaration order."""
    return tuple(v for v in library if v.provider == provider)


def get_voice(
    voice_id: str,
    provider: VoiceProvider,
    *,
    library: tuple[CuratedVoice, ...] = VOICE_LIBRARY,
) -> CuratedVoice:
    """Lookup by `(voice_id, provider)` — raises `UnknownVoiceError` if absent."""
    for voice in library:
        if voice.provider == provider and voice.voice_id == voice_id:
            return voice
    msg = f"voice_id={voice_id!r} not present in curated library for provider={provider!r}"
    raise UnknownVoiceError(msg)
