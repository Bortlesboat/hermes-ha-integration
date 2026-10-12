"""Request-scoped activity contract, independent of diagnostic polling."""

import asyncio
import json
import unittest
import uuid
from types import SimpleNamespace
from unittest import mock

from tests.test_support import (
    FakeChatLog,
    FakeConfigEntry,
    FakeConversationInput,
    FakeHass,
)
from custom_components.hermes_conversation.api import (
    HermesApiClient,
    HermesApiError,
    HermesStreamSetupError,
)
from custom_components.hermes_conversation.const import DOMAIN
from tests.test_api import FakeResponse, FakeSession


class ActivityTests(unittest.IsolatedAsyncioTestCase):
    async def test_overlap_reverse_finish_idempotence_and_privacy(self):
        from custom_components.hermes_conversation.activity import (
            ConversationActivityTracker,
        )

        hass = FakeHass()
        conversation_activity = ConversationActivityTracker(hass, "entry-1")
        snapshots = []
        remove = conversation_activity.subscribe(
            lambda: snapshots.append(conversation_activity.active_request_count)
        )
        first = conversation_activity.start()
        second = conversation_activity.start()
        self.assertEqual(conversation_activity.active_request_count, 2)
        self.assertEqual(
            conversation_activity.finish(
                second, "partial", error_category="secret-key"
            ),
            True,
        )
        self.assertFalse(conversation_activity.finish(second, "completed"))
        # Untrusted categories can be malformed even if internal callers are typed.
        self.assertTrue(
            conversation_activity.finish(first, "completed", error_category=["secret"])
        )  # type: ignore[arg-type]
        self.assertEqual(snapshots, [1, 2, 1, 0])
        self.assertEqual(conversation_activity.last_request_result, "completed")
        self.assertGreaterEqual(conversation_activity.last_request_duration_seconds, 0)
        self.assertEqual(
            [e.data["active_requests"] for e in hass.bus.events], [1, 2, 1, 0]
        )
        self.assertEqual(
            [e.data["status"] for e in hass.bus.events],
            ["started", "started", "partial", "completed"],
        )
        self.assertEqual(
            hass.bus.events[0].data["request_id"], hass.bus.events[3].data["request_id"]
        )
        uuid.UUID(first.request_id)
        self.assertNotIn("secret-key", json.dumps([e.data for e in hass.bus.events]))
        remove()
        conversation_activity.start()
        self.assertEqual(snapshots, [1, 2, 1, 0])

    async def test_shutdown_blocks_late_finishes_and_event_listener_failures(self):
        from custom_components.hermes_conversation.activity import (
            ConversationActivityTracker,
        )

        hass = FakeHass()
        conversation_activity = ConversationActivityTracker(hass, "entry-1")
        conversation_activity.subscribe(
            lambda: (_ for _ in ()).throw(RuntimeError("listener"))
        )
        hass.bus.fail = True
        token = conversation_activity.start()
        self.assertEqual(conversation_activity.active_request_count, 1)
        conversation_activity.shutdown()
        self.assertEqual(conversation_activity.active_request_count, 0)
        self.assertFalse(conversation_activity.finish(token, "completed"))
        self.assertIsNone(conversation_activity.start())

    async def test_entry_unload_recreates_empty_tracker(self):
        from custom_components.hermes_conversation import (
            async_setup_entry,
            async_unload_entry,
        )
        from tests.test_support import FakeHass, FakeConfigEntry

        hass = FakeHass()
        entry = FakeConfigEntry()
        await async_setup_entry(hass, entry)
        old = hass.data[DOMAIN][entry.entry_id]["conversation_activity"]
        token = old.start()
        await async_unload_entry(hass, entry)
        await entry.async_execute_unload()
        self.assertEqual(old.active_request_count, 0)
        self.assertEqual(old.last_request_result, "cancelled")
        self.assertFalse(old.finish(token, "completed"))
        await async_setup_entry(hass, entry)
        new = hass.data[DOMAIN][entry.entry_id]["conversation_activity"]
        self.assertIsNot(new, old)
        self.assertIsNone(new.last_request_result)
        self.assertEqual(new.active_request_count, 0)
        await async_unload_entry(hass, entry)
        await entry.async_execute_unload()


class ConversationActivityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from custom_components.hermes_conversation.activity import (
            ConversationActivityTracker,
        )
        from custom_components.hermes_conversation.conversation import (
            HermesConversationAgent,
        )

        self.hass = FakeHass()
        self.entry = FakeConfigEntry(
            options={"prompt": "", "enable_session_reuse": False}
        )
        self.conversation_activity = ConversationActivityTracker(
            self.hass, self.entry.entry_id
        )
        self.hass.data[DOMAIN] = {
            self.entry.entry_id: {"conversation_activity": self.conversation_activity}
        }
        self.agent_type = HermesConversationAgent

    def agent(self, client):
        return self.agent_type(self.hass, self.entry, client, {})

    async def test_success_failure_and_setup_fallback_are_one_request_each(self):
        class Client:
            last_session_id = None

            def __init__(self):
                self.calls = 0

            async def async_stream_message(self, *args, **kwargs):
                self.calls += 1
                raise HermesStreamSetupError("setup")
                yield

            async def async_send_message(self, *args, **kwargs):
                self.calls += 1
                return SimpleNamespace(text="hello")

        client = Client()
        result = await self.agent(client).async_process(
            FakeConversationInput("private prompt")
        )
        self.assertEqual(result.response.speech["plain"]["speech"], "hello")
        self.assertEqual(client.calls, 2)
        self.assertEqual(
            [e.data["status"] for e in self.hass.bus.events], ["started", "completed"]
        )
        self.assertNotIn("private prompt", str([e.data for e in self.hass.bus.events]))

        async def broken(*args, **kwargs):
            raise HermesApiError("secret")
            yield

        client.async_stream_message = broken
        result = await self.agent(client).async_process(FakeConversationInput("second"))
        self.assertIsNotNone(result.response.error)
        self.assertEqual(self.conversation_activity.last_request_result, "failed")
        self.assertEqual(self.conversation_activity.active_request_count, 0)
        self.assertEqual(
            self.hass.bus.events[-1].data.get("error_category"), "unreachable"
        )

    async def test_http_error_after_stream_post_never_replays_turn(self):
        from custom_components.hermes_conversation import (
            conversation as conversation_module,
        )

        for legacy in (False, True):
            for status in (400, 429, 500, 503):
                with self.subTest(legacy=legacy, status=status):
                    session = FakeSession(
                        [
                            FakeResponse(status=status, text_data="synthetic failure"),
                            FakeResponse(
                                json_data={
                                    "choices": [{"message": {"content": "replayed"}}]
                                }
                            ),
                        ]
                    )
                    client = HermesApiClient(session, host="agent.local", port=8443)
                    conversation_activity = self.conversation_activity
                    with mock.patch.object(
                        conversation_module,
                        "async_get_chat_log",
                        None if legacy else conversation_module.async_get_chat_log,
                    ):
                        result = await self.agent(client).async_process(
                            FakeConversationInput("private turn")
                        )
                    self.assertIsNotNone(result.response.error)
                    self.assertEqual(
                        conversation_activity.last_request_result, "failed"
                    )
                    self.assertEqual(
                        len(session.calls),
                        1,
                        "an HTTP error cannot prove the first POST did not execute tools",
                    )
                    self.assertTrue(
                        session.calls[0]["url"].endswith("/v1/chat/completions")
                    )
                    self.assertTrue(session.calls[0]["json"]["stream"])
                    self.assertEqual(self.hass.bus.events[-1].data["status"], "failed")

    async def test_server_terminal_error_and_incomplete_lifecycle_never_replays_turn(
        self,
    ):
        from custom_components.hermes_conversation import (
            conversation as conversation_module,
        )

        for reason, hermes in (
            ("error", {"failed": True}),
            ("length", {"completed": False, "partial": True}),
        ):
            for prior_content in (False, True):
                for legacy in (False, True):
                    with self.subTest(
                        reason=reason, prior_content=prior_content, legacy=legacy
                    ):
                        chunks = []
                        if prior_content:
                            chunks.append(
                                "data: "
                                + json.dumps(
                                    {
                                        "choices": [
                                            {"delta": {"content": "safe content"}}
                                        ]
                                    }
                                )
                                + "\n\n"
                            )
                        chunks += [
                            "data: "
                            + json.dumps(
                                {
                                    "choices": [{"finish_reason": reason, "delta": {}}],
                                    "hermes": {
                                        **hermes,
                                        "error": "private-server-detail",
                                    },
                                    "error": "private-server-detail",
                                }
                            )
                            + "\n\n",
                            "data: [DONE]\n\n",
                        ]
                        session = FakeSession([FakeResponse(chunks=chunks)])
                        client = HermesApiClient(session, host="agent.local", port=8443)
                        with (
                            mock.patch.object(
                                conversation_module,
                                "async_get_chat_log",
                                None
                                if legacy
                                else conversation_module.async_get_chat_log,
                            ),
                            self.assertLogs(
                                "custom_components.hermes_conversation.conversation"
                            ) as logged,
                        ):
                            result = await self.agent(client).async_process(
                                FakeConversationInput("private prompt")
                            )
                        expected = (
                            "partial" if prior_content and not legacy else "failed"
                        )
                        self.assertEqual(
                            self.conversation_activity.last_request_result, expected
                        )
                        self.assertEqual(
                            self.hass.bus.events[-1].data["status"], expected
                        )
                        self.assertEqual(
                            self.hass.bus.events[-1].data["error_category"], "server"
                        )
                        self.assertEqual(len(session.calls), 1)
                        self.assertNotIn(
                            "private-server-detail",
                            str(result.response.error)
                            + str([e.data for e in self.hass.bus.events])
                            + str(logged.output),
                        )
                        if expected == "partial":
                            self.assertEqual(
                                result.response.speech["plain"]["speech"],
                                "safe content",
                            )
                        else:
                            self.assertIsNotNone(result.response.error)

    async def test_legacy_setup_error_after_delta_never_replays_turn(self):
        from custom_components.hermes_conversation import (
            conversation as conversation_module,
        )

        class Client:
            last_session_id = None

            def __init__(self):
                self.calls = []

            async def async_stream_message(self, *args, **kwargs):
                self.calls.append("stream")
                yield "partial"
                raise HermesStreamSetupError("late setup error")

            async def async_send_message(self, *args, **kwargs):
                self.calls.append("send")
                return SimpleNamespace(text="replayed")

        client = Client()
        with mock.patch.object(conversation_module, "async_get_chat_log", None):
            result = await self.agent(client).async_process(
                FakeConversationInput("private turn")
            )
        self.assertIsNotNone(result.response.error)
        self.assertEqual(self.conversation_activity.last_request_result, "failed")
        self.assertEqual(client.calls, ["stream"])

    async def test_modern_partial_after_content_and_legacy_failure(self):
        class Client:
            last_session_id = None

            async def async_stream_message(self, *args, **kwargs):
                yield "some content"
                raise HermesApiError("sensitive")

        agent = self.agent(Client())
        result = await agent._async_handle_message(
            FakeConversationInput("secret"), FakeChatLog("chat", "secret")
        )
        self.assertEqual(result.response.speech["plain"]["speech"], "some content")
        self.assertEqual(self.conversation_activity.last_request_result, "partial")
        self.assertEqual(
            self.hass.bus.events[-1].data.get("error_category"), "unreachable"
        )
        from custom_components.hermes_conversation import (
            conversation as conversation_module,
        )

        with mock.patch.object(conversation_module, "async_get_chat_log", None):
            result = await agent.async_process(FakeConversationInput("secret"))
        self.assertIsNotNone(result.response.error)
        self.assertEqual(self.conversation_activity.last_request_result, "failed")

    async def test_concurrent_completion_and_cancellation(self):
        entered = asyncio.Event()
        release = asyncio.Event()

        class Client:
            last_session_id = None

            async def async_stream_message(self, messages, session_id=None):
                if messages[-1]["content"] == "slow":
                    entered.set()
                    await release.wait()
                yield "ok"

        agent = self.agent(Client())
        slow = asyncio.create_task(agent.async_process(FakeConversationInput("slow")))
        await entered.wait()
        self.assertEqual(self.conversation_activity.active_request_count, 1)
        await agent.async_process(FakeConversationInput("fast"))
        self.assertEqual(self.conversation_activity.active_request_count, 1)
        release.set()
        await slow
        self.assertEqual(
            [e.data["active_requests"] for e in self.hass.bus.events], [1, 2, 1, 0]
        )
        release.clear()
        entered.clear()
        slow = asyncio.create_task(agent.async_process(FakeConversationInput("slow")))
        await entered.wait()
        slow.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await slow
        self.assertEqual(self.conversation_activity.last_request_result, "cancelled")
        self.assertEqual(self.conversation_activity.active_request_count, 0)

    async def test_incomplete_sse_after_content_is_not_success(self):
        from tests.test_api import FakeResponse, FakeSession
        from custom_components.hermes_conversation.api import HermesApiClient

        response = FakeResponse(
            chunks=['data: {"choices":[{"delta":{"content":"hello"}}]}\n\n']
        )
        client = HermesApiClient(
            session=FakeSession([response]), host="agent.local", port=8080
        )
        with self.assertRaises(HermesApiError):
            async for _ in client.async_stream_message(
                [{"role": "user", "content": "hello"}]
            ):
                pass

    async def test_activity_entities_push_without_diagnostic_refresh(self):
        from custom_components.hermes_conversation.binary_sensor import (
            HermesConversationProcessingBinarySensor,
        )
        from custom_components.hermes_conversation.sensor import (
            HermesActiveConversationRequestsSensor,
            HermesLastConversationDurationSensor,
            HermesLastConversationResultSensor,
        )

        entities = [
            HermesConversationProcessingBinarySensor(
                self.conversation_activity, self.entry
            ),
            HermesActiveConversationRequestsSensor(
                self.conversation_activity, self.entry
            ),
            HermesLastConversationDurationSensor(
                self.conversation_activity, self.entry
            ),
            HermesLastConversationResultSensor(self.conversation_activity, self.entry),
        ]
        for entity in entities:
            await entity.async_added_to_hass()
            self.assertFalse(entity._attr_should_poll)
            self.assertEqual(
                entity.device_info["identifiers"], {(DOMAIN, self.entry.entry_id)}
            )
            self.assertEqual(len(entity._remove_callbacks), 1)
        self.assertFalse(entities[0].is_on)
        self.assertEqual(entities[1].native_value, 0)
        self.assertFalse(entities[2].available)
        self.assertFalse(entities[3].available)
        token = self.conversation_activity.start()
        self.assertTrue(entities[0].is_on)
        self.assertEqual(entities[1].native_value, 1)
        self.conversation_activity.finish(token, "completed")
        self.assertEqual(entities[3].native_value, "completed")
        self.assertGreaterEqual(entities[2].native_value, 0)
        self.assertEqual([entity.written_states for entity in entities], [2] * 4)
        for entity in entities:
            await entity.async_will_remove_from_hass()
        self.conversation_activity.start()
        self.assertEqual([entity.written_states for entity in entities], [2] * 4)

    async def test_subscription_telemetry_failure_does_not_retry_conversation(self):
        class Client:
            last_session_id = None
            calls = 0

            async def async_stream_message(self, *args, **kwargs):
                self.calls += 1
                yield "ok"

        client = Client()
        self.hass.bus.fail = True
        self.conversation_activity.subscribe(
            lambda: (_ for _ in ()).throw(RuntimeError("observer"))
        )
        result = await self.agent(client).async_process(
            FakeConversationInput("private")
        )
        self.assertEqual(result.response.speech["plain"]["speech"], "ok")
        self.assertEqual(client.calls, 1)
        self.assertEqual(self.conversation_activity.active_request_count, 0)
        self.assertEqual(self.conversation_activity.last_request_result, "completed")

    async def test_unexpected_error_and_unloaded_tracker(self):
        agent = self.agent(SimpleNamespace(last_session_id=None))
        agent._render_system_prompt = lambda name: (_ for _ in ()).throw(
            ValueError("private")
        )
        result = await agent.async_process(FakeConversationInput("secret"))
        self.assertIsNotNone(result.response.error)
        self.assertEqual(self.conversation_activity.last_request_result, "failed")
        self.conversation_activity.shutdown()
        before = len(self.hass.bus.events)
        await agent.async_process(FakeConversationInput("secret"))
        self.assertEqual(len(self.hass.bus.events), before)
