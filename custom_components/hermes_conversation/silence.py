"""The canonical Hermes intentional-silence marker at the speech boundary."""

SILENCE_MARKER = "NO_REPLY"
_ASCII_SPACE = " \t\r\n\v\f"
_MAX_PREFIX_CHARS = 64  # Bound a stream of leading/trailing whitespace.


def is_silence(text: str) -> bool:
    """Only a whole marker is control data; mentions remain ordinary speech."""
    return len(text) <= _MAX_PREFIX_CHARS and text.strip(_ASCII_SPACE) == SILENCE_MARKER


class SpeechSilenceFilter:
    """Hold a possible marker until stream end, releasing prose on divergence."""

    def __init__(self) -> None:
        self._pending = ""
        self._passthrough = False
        self.silenced = False

    def feed(self, text: str) -> str:
        if self._passthrough:
            return text
        self._pending += text
        candidate = self._pending.strip(_ASCII_SPACE)
        if len(self._pending) <= _MAX_PREFIX_CHARS and SILENCE_MARKER.startswith(candidate):
            return ""
        self._passthrough = True
        result, self._pending = self._pending, ""
        return result

    def flush(self) -> str:
        result, self._pending = self._pending, ""
        self.silenced = is_silence(result)
        return "" if self.silenced else result
