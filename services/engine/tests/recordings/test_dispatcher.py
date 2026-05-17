"""Unit tests for `LiveKitEgressDispatcher` + `NullEgressDispatcher`.

Strategy: substitute a fake `LiveKitAPI` whose `.egress` attribute is a
`FakeEgressService` recording every call. The dispatcher's only contract
with LiveKit is through the `start_room_composite_egress` /
`start_participant_egress` coroutines, so we exercise that boundary
without touching the network.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

import pytest

from verbio_engine.recordings import (
    EgressHandle,
    LiveKitEgressDispatcher,
    NullEgressDispatcher,
    R2EgressConfig,
    composite_audio_key,
    participant_audio_key,
)

R2 = R2EgressConfig(
    account_id="acct",
    access_key_id="ak",
    secret_access_key="sk",  # noqa: S106 — synthetic test value
    bucket="verbio-test",
)


@dataclass(slots=True)
class _FakeEgressInfo:
    """Minimal `EgressInfo` substitute — only `.egress_id` is read."""

    egress_id: str


@dataclass(slots=True)
class _FakeEgressService:
    """Records every Start*Egress call; raises if instructed."""

    raise_on_composite: bool = False
    raise_on_participant: bool = False
    composite_calls: list[Any] = field(default_factory=list)
    participant_calls: list[Any] = field(default_factory=list)
    next_id: int = 0

    def _mint(self) -> str:
        self.next_id += 1
        return f"EG_{self.next_id:04d}"

    async def start_room_composite_egress(self, request: Any) -> _FakeEgressInfo:
        if self.raise_on_composite:
            msg = "LiveKit composite egress refused (simulated)"
            raise RuntimeError(msg)
        self.composite_calls.append(request)
        return _FakeEgressInfo(egress_id=self._mint())

    async def start_participant_egress(self, request: Any) -> _FakeEgressInfo:
        if self.raise_on_participant:
            msg = "LiveKit participant egress refused (simulated)"
            raise RuntimeError(msg)
        self.participant_calls.append(request)
        return _FakeEgressInfo(egress_id=self._mint())


@dataclass(slots=True)
class _FakeLiveKitAPI:
    """`LiveKitAPI` substitute exposing only `.egress` + `.aclose`."""

    egress: _FakeEgressService
    closed: bool = False

    async def aclose(self) -> None:
        self.closed = True


def _make_dispatcher(
    *,
    service: _FakeEgressService | None = None,
) -> tuple[LiveKitEgressDispatcher, _FakeLiveKitAPI, list[int]]:
    """Wire a dispatcher to a fake LiveKitAPI and track factory invocations."""
    api = _FakeLiveKitAPI(egress=service or _FakeEgressService())
    factory_calls: list[int] = []

    def _factory() -> Any:
        factory_calls.append(1)
        return api

    dispatcher = LiveKitEgressDispatcher(r2=R2, api_factory=_factory)
    return dispatcher, api, factory_calls


async def test_null_dispatcher_returns_none_for_all_starts() -> None:
    null = NullEgressDispatcher()
    sid = uuid.uuid4()

    assert await null.start_room_composite(session_id=sid, room_name="room-1") is None
    assert (
        await null.start_participant_audio(
            session_id=sid,
            room_name="room-1",
            identity="p1",
        )
        is None
    )

    await null.aclose()


async def test_start_room_composite_returns_handle_with_egress_id_and_key() -> None:
    dispatcher, _api, _ = _make_dispatcher()
    sid = uuid.uuid4()

    handle = await dispatcher.start_room_composite(session_id=sid, room_name="room-7")

    assert isinstance(handle, EgressHandle)
    assert handle.egress_id == "EG_0001"
    assert handle.r2_key == composite_audio_key(sid)


async def test_composite_request_carries_r2_destination_and_filepath() -> None:
    dispatcher, api, _ = _make_dispatcher()
    sid = uuid.uuid4()

    await dispatcher.start_room_composite(session_id=sid, room_name="room-7")

    assert len(api.egress.composite_calls) == 1
    request = api.egress.composite_calls[0]
    assert request.room_name == "room-7"
    assert len(request.file_outputs) == 1
    output = request.file_outputs[0]
    assert output.filepath == composite_audio_key(sid)
    s3 = output.s3
    assert s3.bucket == R2.bucket
    assert s3.access_key == R2.access_key_id
    assert s3.secret == R2.secret_access_key
    assert s3.region == "auto"
    assert s3.endpoint == R2.endpoint
    assert s3.force_path_style is True


async def test_composite_is_idempotent_per_session() -> None:
    dispatcher, api, _ = _make_dispatcher()
    sid = uuid.uuid4()

    first = await dispatcher.start_room_composite(session_id=sid, room_name="room-7")
    second = await dispatcher.start_room_composite(session_id=sid, room_name="room-7")

    assert first is not None
    assert second is None
    assert len(api.egress.composite_calls) == 1


async def test_composite_failure_rolls_back_dedup_to_allow_retry() -> None:
    service = _FakeEgressService(raise_on_composite=True)
    dispatcher, api, _ = _make_dispatcher(service=service)
    sid = uuid.uuid4()

    first = await dispatcher.start_room_composite(session_id=sid, room_name="room-7")
    assert first is None
    assert api.egress.composite_calls == []

    # Flip the simulated outage off and confirm a retry now lands.
    service.raise_on_composite = False
    retry = await dispatcher.start_room_composite(session_id=sid, room_name="room-7")
    assert retry is not None
    assert retry.r2_key == composite_audio_key(sid)


async def test_start_participant_audio_returns_handle_and_request_shape() -> None:
    dispatcher, api, _ = _make_dispatcher()
    sid = uuid.uuid4()

    handle = await dispatcher.start_participant_audio(
        session_id=sid,
        room_name="room-7",
        identity="alice-42",
    )

    assert handle is not None
    assert handle.egress_id == "EG_0001"
    assert handle.r2_key == participant_audio_key(sid, "alice-42")

    assert len(api.egress.participant_calls) == 1
    request = api.egress.participant_calls[0]
    assert request.room_name == "room-7"
    assert request.identity == "alice-42"
    output = request.file_outputs[0]
    assert output.filepath == participant_audio_key(sid, "alice-42")
    assert output.s3.bucket == R2.bucket
    assert output.s3.endpoint == R2.endpoint


async def test_participant_failure_swallows_and_returns_none() -> None:
    service = _FakeEgressService(raise_on_participant=True)
    dispatcher, api, _ = _make_dispatcher(service=service)
    sid = uuid.uuid4()

    handle = await dispatcher.start_participant_audio(
        session_id=sid,
        room_name="room-7",
        identity="alice-42",
    )

    assert handle is None
    assert api.egress.participant_calls == []


async def test_api_factory_is_only_invoked_once_across_calls() -> None:
    """Lazy singleton: aiohttp session is shared across egress starts."""
    dispatcher, _api, calls = _make_dispatcher()
    sid_a, sid_b = uuid.uuid4(), uuid.uuid4()

    await dispatcher.start_room_composite(session_id=sid_a, room_name="room-7")
    await dispatcher.start_participant_audio(
        session_id=sid_a,
        room_name="room-7",
        identity="alice",
    )
    await dispatcher.start_room_composite(session_id=sid_b, room_name="room-8")

    assert len(calls) == 1


async def test_api_factory_not_invoked_until_first_start() -> None:
    """`aclose` on an unused dispatcher is a no-op."""
    dispatcher, _api, calls = _make_dispatcher()

    await dispatcher.aclose()

    assert calls == []


async def test_aclose_invokes_api_aclose_after_use() -> None:
    dispatcher, api, _ = _make_dispatcher()
    sid = uuid.uuid4()

    await dispatcher.start_room_composite(session_id=sid, room_name="room-7")
    await dispatcher.aclose()

    assert api.closed is True


async def test_aclose_is_idempotent() -> None:
    dispatcher, api, _ = _make_dispatcher()
    sid = uuid.uuid4()

    await dispatcher.start_room_composite(session_id=sid, room_name="room-7")
    await dispatcher.aclose()
    await dispatcher.aclose()  # second call must not raise

    assert api.closed is True


async def test_two_participants_same_session_make_two_distinct_starts() -> None:
    dispatcher, api, _ = _make_dispatcher()
    sid = uuid.uuid4()

    h1 = await dispatcher.start_participant_audio(
        session_id=sid,
        room_name="room-7",
        identity="alice",
    )
    h2 = await dispatcher.start_participant_audio(
        session_id=sid,
        room_name="room-7",
        identity="bob",
    )

    assert h1 is not None and h2 is not None
    assert h1.r2_key != h2.r2_key
    assert h1.egress_id != h2.egress_id
    assert len(api.egress.participant_calls) == 2


@pytest.mark.parametrize("identity", ["alice", "bob_99", "p.q.r", "team-lead"])
async def test_participant_key_format_matches_helper(identity: str) -> None:
    dispatcher, _api, _ = _make_dispatcher()
    sid = uuid.uuid4()

    handle = await dispatcher.start_participant_audio(
        session_id=sid,
        room_name="room-7",
        identity=identity,
    )

    assert handle is not None
    assert handle.r2_key == participant_audio_key(sid, identity)
