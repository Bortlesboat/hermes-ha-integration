"""Real Home Assistant activity checks against a synthetic loopback Hermes API."""

from __future__ import annotations

import asyncio

import pytest
from homeassistant.components import conversation
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hermes_conversation.const import (
    CONF_API_KEY,
    CONF_HOST,
    CONF_PORT,
    CONF_USE_SSL,
    DOMAIN,
)
from runtime_tests.loopback import API_KEY, HermesLoopbackApi


_EVENT_FIELDS = {
    "entry_id",
    "request_id",
    "source",
    "status",
    "active_requests",
    "duration_seconds",
    "error_category",
}


def _assert_private_correlated_events(events, entry_id, *prompts):
    assert len(events) % 2 == 0
    request_ids = []
    for start, finish in zip(events[::2], events[1::2]):
        start, finish = start.data, finish.data
        assert start["status"] == "started"
        assert start["active_requests"] == 1
        assert finish["active_requests"] == 0
        assert isinstance(finish["duration_seconds"], (int, float))
        assert finish["duration_seconds"] >= 0
        assert start["request_id"] == finish["request_id"]
        request_ids.append(start["request_id"])
    assert len(set(request_ids)) == len(request_ids)
    for event in events:
        data = event.data
        assert set(data) <= _EVENT_FIELDS
        assert data["entry_id"] == entry_id
        assert data["source"] == "home_assistant"
        assert API_KEY not in str(data)
        assert all(prompt not in str(data) for prompt in prompts)


def _id(hass, domain, entry_id, key):
    value = er.async_get(hass).async_get_entity_id(domain, DOMAIN, f"{entry_id}_{key}")
    assert value is not None
    return value


async def _setup(hass, api):
    entry = MockConfigEntry(
        domain=DOMAIN,
        entry_id="activity-entry",
        title="Hermes Agent",
        data={
            CONF_HOST: "127.0.0.1",
            CONF_PORT: api.port,
            CONF_API_KEY: API_KEY,
            CONF_USE_SSL: False,
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    return entry


async def _process(hass, entry, text):
    return await conversation.async_converse(
        hass,
        text,
        context=Context(),
        conversation_id=None,
        agent_id=er.async_get(hass).async_get_entity_id(
            "conversation", DOMAIN, entry.entry_id
        ),
    )


async def test_activity_registry_and_real_conversation_lifecycle(
    hass_ready: HomeAssistant, hermes_api: HermesLoopbackApi
):
    hass = hass_ready
    hermes_api.chat_response = "synthetic hello"
    entry = await _setup(hass, hermes_api)
    ids = {
        key: _id(hass, domain, entry.entry_id, key)
        for domain, key in (
            ("binary_sensor", "ha_request_in_progress"),
            ("sensor", "active_ha_requests"),
            ("sensor", "last_ha_request_duration"),
            ("sensor", "last_ha_request_outcome"),
        )
    }
    devices = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    assert len(devices) == 1
    for entity_id in ids.values():
        item = er.async_get(hass).async_get(entity_id)
        assert item is not None and item.device_id == devices[0].id
    assert hass.states.get(ids["ha_request_in_progress"]).state == "off"
    assert hass.states.get(ids["active_ha_requests"]).state == "0"
    assert hass.states.get(ids["last_ha_request_duration"]).state == STATE_UNAVAILABLE
    assert hass.states.get(ids["last_ha_request_outcome"]).state == STATE_UNAVAILABLE
    events = []
    remove = hass.bus.async_listen("hermes_conversation_activity", events.append)
    diagnostics_before = hermes_api.count_paths(
        "/v1/health", "/v1/models", "/health/detailed"
    )
    try:
        result = await _process(hass, entry, "private prompt")
        await hass.async_block_till_done()
        assert (
            hermes_api.count_paths("/v1/health", "/v1/models", "/health/detailed")
            == diagnostics_before
        )
        assert result.response.speech["plain"]["speech"] == "synthetic hello"
        assert [event.data["status"] for event in events] == ["started", "completed"]
        assert events[0].data["request_id"] == events[1].data["request_id"]
        assert all("private prompt" not in str(event.data) for event in events)
        assert hass.states.get(ids["ha_request_in_progress"]).state == "off"
        assert hass.states.get(ids["active_ha_requests"]).state == "0"
        assert hass.states.get(ids["last_ha_request_outcome"]).state == "completed"
        assert float(hass.states.get(ids["last_ha_request_duration"]).state) >= 0
        # The real HA conversation API determines which path this invocation uses.
        chat_log = getattr(conversation, "ChatLog", None)
        modern_chat_log = chat_log is not None and hasattr(
            conversation, "async_get_chat_log"
        )
        if modern_chat_log:
            assert chat_log.__module__.startswith("homeassistant.")
        hermes_api.chat_response = "synthetic partial speech"
        hermes_api.chat_partial = True
        partial_before = hermes_api.count_paths("/v1/chat/completions")
        partial_result = await _process(hass, entry, "another private prompt")
        await hass.async_block_till_done()
        assert hermes_api.count_paths("/v1/chat/completions") == partial_before + 1
        assert hass.states.get(ids["last_ha_request_outcome"]).state == (
            "partial" if modern_chat_log else "failed"
        )
        assert events[3].data["status"] == ("partial" if modern_chat_log else "failed")
        if modern_chat_log:
            assert (
                partial_result.response.speech["plain"]["speech"]
                == "synthetic partial speech"
            )
        else:
            assert (
                partial_result.response.speech["plain"]["speech"]
                != "synthetic partial speech"
            )
        hermes_api.chat_partial = False
        hermes_api.chat_status = 401
        auth_before = hermes_api.count_paths("/v1/chat/completions")
        await _process(hass, entry, "failed private prompt")
        await hass.async_block_till_done()
        assert hermes_api.count_paths("/v1/chat/completions") == auth_before + 1
        assert hass.states.get(ids["last_ha_request_outcome"]).state == "failed"
        assert [event.data["status"] for event in events] == [
            "started",
            "completed",
            "started",
            "partial" if modern_chat_log else "failed",
            "started",
            "failed",
        ]
        assert events[5].data["error_category"] == "auth"
        assert (
            hermes_api.count_paths("/v1/health", "/v1/models", "/health/detailed")
            == diagnostics_before
        )
        _assert_private_correlated_events(
            events,
            entry.entry_id,
            "private prompt",
            "another private prompt",
            "failed private prompt",
        )
        assert await hass.config_entries.async_unload(entry.entry_id)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done(wait_background_tasks=True)
        assert (
            hass.states.get(ids["last_ha_request_outcome"]).state == STATE_UNAVAILABLE
        )
        assert hass.states.get(ids["active_ha_requests"]).state == "0"
    finally:
        remove()


async def test_ambiguous_http_failure_does_not_replay_real_ha_turn(
    hass_ready: HomeAssistant,
    hermes_api: HermesLoopbackApi,
):
    hass = hass_ready
    entry = await _setup(hass, hermes_api)
    outcome = _id(hass, "sensor", entry.entry_id, "last_ha_request_outcome")
    events = []
    remove = hass.bus.async_listen("hermes_conversation_activity", events.append)
    try:
        for status in (400, 403, 429, 500, 503):
            hermes_api.chat_status = status
            before = hermes_api.count_paths("/v1/chat/completions")
            result = await _process(hass, entry, f"private HTTP {status} turn")
            await hass.async_block_till_done()
            assert (
                result.response.response_type
                == conversation.intent.IntentResponseType.ERROR
            )
            chat_requests = [
                request
                for request in hermes_api.requests
                if "/v1/chat/completions" in request.path
            ]
            assert [request.method for request in chat_requests[before:]] == ["POST"]
            assert hass.states.get(outcome).state == "failed"
            assert [event.data["status"] for event in events[-2:]] == [
                "started",
                "failed",
            ]
            assert events[-1].data["error_category"] == (
                "auth"
                if status == 403
                else "server"
                if status >= 500
                else "unreachable"
            )
        _assert_private_correlated_events(
            events,
            entry.entry_id,
            *(f"private HTTP {status} turn" for status in (400, 403, 429, 500, 503)),
        )
    finally:
        remove()


async def test_server_terminal_error_and_incomplete_real_ha_no_replay(
    hass_ready: HomeAssistant,
    hermes_api: HermesLoopbackApi,
):
    hass = hass_ready
    entry = await _setup(hass, hermes_api)
    outcome = _id(hass, "sensor", entry.entry_id, "last_ha_request_outcome")
    chat_log = getattr(conversation, "ChatLog", None)
    modern = chat_log is not None and hasattr(conversation, "async_get_chat_log")
    events = []
    remove = hass.bus.async_listen("hermes_conversation_activity", events.append)
    secret = "private-server-detail"
    try:
        for reason, hermes in (
            ("error", {"failed": True, "completed": False}),
            ("length", {"partial": True, "completed": False}),
        ):
            for prior_content in (False, True):
                hermes_api.chat_response = (
                    "synthetic partial speech" if prior_content else ""
                )
                hermes_api.chat_finish_reason = reason
                hermes_api.chat_terminal_hermes = {**hermes, "error": secret}
                hermes_api.chat_terminal_error = secret
                before = hermes_api.count_paths("/v1/chat/completions")
                result = await _process(hass, entry, f"private {reason} prompt")
                await hass.async_block_till_done()
                assert hermes_api.count_paths("/v1/chat/completions") == before + 1
                expected = "partial" if prior_content and modern else "failed"
                assert hass.states.get(outcome).state == expected
                assert [event.data["status"] for event in events[-2:]] == [
                    "started",
                    expected,
                ]
                assert events[-1].data["error_category"] == "server"
                assert secret not in str(result.response.as_dict())
                if expected == "partial":
                    assert (
                        result.response.speech["plain"]["speech"]
                        == "synthetic partial speech"
                    )
                else:
                    assert (
                        result.response.response_type
                        == conversation.intent.IntentResponseType.ERROR
                    )
        _assert_private_correlated_events(
            events,
            entry.entry_id,
            "private error prompt",
            "private length prompt",
            secret,
        )
    finally:
        remove()


async def test_server_terminal_error_without_done_real_ha_no_replay(
    hass_ready: HomeAssistant,
    hermes_api: HermesLoopbackApi,
):
    hass = hass_ready
    entry = await _setup(hass, hermes_api)
    outcome = _id(hass, "sensor", entry.entry_id, "last_ha_request_outcome")
    modern = getattr(conversation, "ChatLog", None) is not None and hasattr(
        conversation, "async_get_chat_log"
    )
    events = []
    remove = hass.bus.async_listen("hermes_conversation_activity", events.append)
    secret = "private-server-detail"
    try:
        for reason, metadata, error_event in (
            ("error", None, None),
            ("length", None, None),
            (None, {"failed": True}, None),
            (None, {"completed": False}, None),
            (None, None, secret),
        ):
            for prior_content in (False, True):
                hermes_api.chat_response = (
                    "synthetic partial speech" if prior_content else ""
                )
                hermes_api.chat_partial = True
                hermes_api.chat_finish_reason = reason
                hermes_api.chat_terminal_hermes = (
                    {**metadata, "error": secret} if metadata is not None else None
                )
                hermes_api.chat_terminal_error = secret if reason else None
                hermes_api.chat_error_event = error_event
                before = hermes_api.count_paths("/v1/chat/completions")
                result = await _process(hass, entry, "private EOF prompt")
                await hass.async_block_till_done()
                assert hermes_api.count_paths("/v1/chat/completions") == before + 1
                expected = "partial" if prior_content and modern else "failed"
                assert hass.states.get(outcome).state == expected
                assert [e.data["status"] for e in events[-2:]] == ["started", expected]
                assert events[-1].data["error_category"] == "server"
                assert secret not in str(result.response.as_dict())
                if expected == "partial":
                    assert (
                        result.response.speech["plain"]["speech"]
                        == "synthetic partial speech"
                    )
                else:
                    assert (
                        result.response.response_type
                        == conversation.intent.IntentResponseType.ERROR
                    )
        _assert_private_correlated_events(
            events, entry.entry_id, "private EOF prompt", secret
        )
    finally:
        remove()


async def test_real_ha_chat_timeout_category(
    hass_ready: HomeAssistant,
    hermes_api: HermesLoopbackApi,
):
    hass = hass_ready
    entry = await _setup(hass, hermes_api)
    client = hass.data[DOMAIN][entry.entry_id]["client"]
    client._request_timeout = 1
    client._stream_timeout = 1
    outcome = _id(hass, "sensor", entry.entry_id, "last_ha_request_outcome")
    hermes_api.block_chat = asyncio.Event()
    events = []
    remove = hass.bus.async_listen("hermes_conversation_activity", events.append)
    try:
        result = await asyncio.wait_for(
            _process(hass, entry, "private timeout prompt"), 4
        )
        await hass.async_block_till_done()
        assert (
            result.response.response_type
            == conversation.intent.IntentResponseType.ERROR
        )
        assert hass.states.get(outcome).state == "failed"
        assert [e.data["status"] for e in events] == ["started", "failed"]
        assert events[-1].data["error_category"] == "timeout"
        assert hermes_api.count_paths("/v1/chat/completions") == 1
        _assert_private_correlated_events(
            events, entry.entry_id, "private timeout prompt"
        )
    finally:
        hermes_api.block_chat.set()
        remove()


async def test_concurrent_and_cancelled_real_requests(
    hass_ready: HomeAssistant, hermes_api: HermesLoopbackApi
):
    hass = hass_ready
    entry = await _setup(hass, hermes_api)
    active = _id(hass, "sensor", entry.entry_id, "active_ha_requests")
    outcome = _id(hass, "sensor", entry.entry_id, "last_ha_request_outcome")
    release = asyncio.Event()
    hermes_api.block_chat = release
    events = []
    remove = hass.bus.async_listen("hermes_conversation_activity", events.append)
    try:
        first = hass.async_create_task(_process(hass, entry, "private first prompt"))
        await asyncio.wait_for(hermes_api.chat_started.wait(), 5)
        second = hass.async_create_task(_process(hass, entry, "private second prompt"))
        for _ in range(100):
            if hermes_api.count_paths("/v1/chat/completions") == 2:
                break
            await asyncio.sleep(0.01)
        assert hermes_api.count_paths("/v1/chat/completions") == 2
        assert hass.states.get(active).state == "2"
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        assert hass.states.get(active).state == "1"
        assert hass.states.get(outcome).state == "cancelled"
        release.set()
        result = await second
        await hass.async_block_till_done()
        assert result.response.speech["plain"]["speech"] == "synthetic response"
        assert hass.states.get(active).state == "0"
        assert hass.states.get(outcome).state == "completed"
        assert [event.data["active_requests"] for event in events] == [1, 2, 1, 0]
        assert [event.data["status"] for event in events] == [
            "started",
            "started",
            "cancelled",
            "completed",
        ]
        assert events[0].data["request_id"] == events[2].data["request_id"]
        assert events[1].data["request_id"] == events[3].data["request_id"]
        assert events[0].data["request_id"] != events[1].data["request_id"]
        assert all(set(event.data) <= _EVENT_FIELDS for event in events)
        assert all(
            "private first prompt" not in str(event.data)
            and "private second prompt" not in str(event.data)
            and API_KEY not in str(event.data)
            for event in events
        )
    finally:
        release.set()
        remove()


async def test_unload_cancels_in_flight_real_conversation_without_late_events(
    hass_ready: HomeAssistant, hermes_api: HermesLoopbackApi
):
    hass = hass_ready
    entry = await _setup(hass, hermes_api)
    active = _id(hass, "sensor", entry.entry_id, "active_ha_requests")
    in_progress = _id(hass, "binary_sensor", entry.entry_id, "ha_request_in_progress")
    outcome = _id(hass, "sensor", entry.entry_id, "last_ha_request_outcome")
    old_activity = hass.data[DOMAIN][entry.entry_id]["activity"]
    release = asyncio.Event()
    hermes_api.block_chat = release
    events = []
    remove = hass.bus.async_listen("hermes_conversation_activity", events.append)
    request = None
    try:
        request = hass.async_create_task(
            _process(hass, entry, "unload while in flight")
        )
        await asyncio.wait_for(hermes_api.chat_started.wait(), 5)
        assert not request.done()
        assert old_activity.active_requests == 1
        assert hass.states.get(active).state == "1"
        assert hass.states.get(in_progress).state == "on"
        assert [event.data["status"] for event in events] == ["started"]
        request_id = events[0].data["request_id"]

        assert await hass.config_entries.async_unload(entry.entry_id)
        assert not request.done()
        assert old_activity.active_requests == 0
        assert [
            (event.data["status"], event.data["request_id"]) for event in events
        ] == [("started", request_id), ("cancelled", request_id)]
        assert events[1].data["active_requests"] == 0
        assert all(set(event.data) <= _EVENT_FIELDS for event in events)
        assert all(
            "unload while in flight" not in str(event.data)
            and API_KEY not in str(event.data)
            for event in events
        )

        assert await hass.config_entries.async_setup(entry.entry_id)
        new_activity = hass.data[DOMAIN][entry.entry_id]["activity"]
        assert new_activity is not old_activity
        assert new_activity.active_requests == 0
        assert hass.states.get(active).state == "0"
        assert hass.states.get(in_progress).state == "off"
        assert hass.states.get(outcome).state == STATE_UNAVAILABLE
        assert not request.done()

        release.set()
        result = await asyncio.wait_for(request, 5)
        assert result.response.speech["plain"]["speech"] == "synthetic response"
        assert old_activity.active_requests == 0
        assert new_activity.active_requests == 0
        assert hass.states.get(active).state == "0"
        assert hass.states.get(in_progress).state == "off"
        assert hass.states.get(outcome).state == STATE_UNAVAILABLE
        assert [
            (event.data["status"], event.data["request_id"]) for event in events
        ] == [("started", request_id), ("cancelled", request_id)]
    finally:
        release.set()
        if request is not None and not request.done():
            await asyncio.wait_for(request, 5)
        remove()
