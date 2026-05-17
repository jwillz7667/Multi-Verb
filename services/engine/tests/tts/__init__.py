"""Tests for `verbio_engine.tts` — the §9 TTS layer.

P4 L3 covers `CartesiaTTS` with an httpx MockTransport simulating the
Cartesia SSE endpoint. ElevenLabs fallback gets its own module in L4.
"""
