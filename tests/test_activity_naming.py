"""Internal names of unreleased conversation telemetry, independent of HA stubs."""

import unittest

from tests.test_support import FakeHass


class ConversationActivityNamingTests(unittest.TestCase):
    def test_tracker_public_names_and_wire_schema(self):
        from custom_components.hermes_conversation.activity import (
            ACTIVITY_ERROR_CATEGORIES,
            CONVERSATION_RESULTS,
            EVENT_CONVERSATION_ACTIVITY,
            ConversationActivityTracker,
            ConversationRequestToken,
        )

        hass = FakeHass()
        tracker = ConversationActivityTracker(hass, "entry-1")
        assert tracker.active_request_count == 0
        assert tracker.last_request_result is None
        assert tracker.last_request_duration_seconds is None
        token = tracker.start()
        assert isinstance(token, ConversationRequestToken)
        assert tracker.active_request_count == 1
        assert tracker.finish(token, "completed")
        assert tracker.last_request_result == "completed"
        assert tracker.last_request_duration_seconds >= 0
        assert EVENT_CONVERSATION_ACTIVITY == "hermes_conversation_activity"
        assert CONVERSATION_RESULTS == ("completed", "partial", "failed", "cancelled")
        assert "server" in ACTIVITY_ERROR_CATEGORIES
        assert [event.data["active_requests"] for event in hass.bus.events] == [1, 0]
        assert [event.event_type for event in hass.bus.events] == [
            "hermes_conversation_activity",
            "hermes_conversation_activity",
        ]

    def test_new_entities_use_new_class_names(self):
        from custom_components.hermes_conversation.binary_sensor import (
            HermesConversationProcessingBinarySensor,
        )
        from custom_components.hermes_conversation.entity import (
            HermesConversationActivityEntity,
        )
        from custom_components.hermes_conversation.sensor import (
            HermesActiveConversationRequestsSensor,
            HermesLastConversationDurationSensor,
            HermesLastConversationResultSensor,
        )

        for entity_class in (
            HermesConversationProcessingBinarySensor,
            HermesActiveConversationRequestsSensor,
            HermesLastConversationDurationSensor,
            HermesLastConversationResultSensor,
        ):
            assert issubclass(entity_class, HermesConversationActivityEntity)
