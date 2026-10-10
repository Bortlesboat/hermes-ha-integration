"""Fresh entity registry and displayed-name contract on real Home Assistant."""

from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from runtime_tests.test_activity_runtime import _setup


async def test_fresh_conversation_activity_names_and_registry(hass_ready, hermes_api):
    """Only entry-local conversation telemetry is added; published diagnostics stay put."""
    hass = hass_ready
    entry = await _setup(hass, hermes_api)
    expected = {
        "binary_sensor": {
            "api_connectivity": ("api_connectivity", "API connectivity"),
            "processing_conversation": (
                "processing_conversation",
                "Processing conversation",
            ),
        },
        "sensor": {
            "health": ("health", "Health"),
            "api_latency": ("api_latency", "API latency"),
            "last_successful_connection": (
                "last_successful_connection",
                "Last successful connection",
            ),
            "active_conversation_requests": (
                "active_conversation_requests",
                "Active conversation requests",
            ),
            "last_conversation_duration": (
                "last_conversation_duration",
                "Last conversation duration",
            ),
            "last_conversation_result": (
                "last_conversation_result",
                "Last conversation result",
            ),
        },
    }
    registry = er.async_get(hass)
    devices = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    assert len(devices) == 1
    assert devices[0].name == "Hermes Agent"
    entries = [
        item
        for item in er.async_entries_for_config_entry(registry, entry.entry_id)
        if item.domain in ("binary_sensor", "sensor")
    ]
    assert len(entries) == 8
    assert len({item.entity_id for item in entries}) == 8
    assert len({item.unique_id for item in entries}) == 8
    assert {
        item.entity_id: (item.unique_id, item.original_name, item.device_id)
        for item in entries
    } == {
        f"{domain}.hermes_agent_{key}": (
            f"{entry.entry_id}_{unique_suffix}",
            name,
            devices[0].id,
        )
        for domain, domain_keys in expected.items()
        for key, (unique_suffix, name) in domain_keys.items()
    }
    assert not any(
        old in item.unique_id or old in item.entity_id
        for item in entries
        for old in (
            "ha_request_in_progress",
            "active_ha_requests",
            "last_ha_request_duration",
            "last_ha_request_outcome",
        )
    )


async def test_titled_entry_does_not_repeat_device_name(hass_ready, hermes_api):
    """Entity labels compose with nondefault device titles without a second prefix."""
    hass = hass_ready
    entry = await _setup(hass, hermes_api, title="Worker")
    registry = er.async_get(hass)
    devices = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    assert len(devices) == 1
    assert devices[0].name == "Worker"
    labels = {
        "binary_sensor.worker_processing_conversation": "Processing conversation",
        "sensor.worker_active_conversation_requests": "Active conversation requests",
        "sensor.worker_last_conversation_duration": "Last conversation duration",
        "sensor.worker_last_conversation_result": "Last conversation result",
    }
    for entity_id, name in labels.items():
        item = registry.async_get(entity_id)
        assert item is not None
        assert item.original_name == name
        assert item.device_id == devices[0].id
