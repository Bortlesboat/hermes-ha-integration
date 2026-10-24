"""Real HA conversation/ChatLog must accept intentional silence."""
import pytest
from custom_components.hermes_conversation.const import (
    CONF_CONTINUED_CONVERSATION_MODE, FOLLOW_UP_MODE_ALWAYS,
)
from runtime_tests.test_activity_runtime import _setup, _process


@pytest.mark.parametrize('text,silent', [
    ('NO_REPLY', True),
    ('NO_REPLY means no response.', False),
    ('**NO_REPLY**', False),
    ('`NO_REPLY`', False),
])
async def test_real_conversation_silence(hass_ready, hermes_api, text, silent):
    hass = hass_ready
    hermes_api.chat_response = text
    entry = await _setup(hass, hermes_api)
    hass.config_entries.async_update_entry(entry, options={CONF_CONTINUED_CONVERSATION_MODE:FOLLOW_UP_MODE_ALWAYS})
    await hass.async_block_till_done(wait_background_tasks=True)
    before = hermes_api.count_paths('/v1/chat/completions')
    result = await _process(hass, entry, 'Synthetic no-device-change silence boundary test')
    await hass.async_block_till_done()
    assert hermes_api.count_paths('/v1/chat/completions') == before + 1
    assert result.response.as_dict()['response_type'] != 'error'
    if silent:
        assert not result.response.speech
        assert result.continue_conversation is False
    else:
        assert result.response.speech['plain']['speech']
        assert result.continue_conversation is True
        if text in ('**NO_REPLY**', '`NO_REPLY`'):
            assert result.response.speech['plain']['speech'] == 'NO_REPLY'
