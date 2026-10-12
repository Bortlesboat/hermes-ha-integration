# Changelog

## [1.4.0] - 2026-10-12

### Added

- Track conversation requests originating through each Home Assistant integration entry (Assist or `conversation.process`) with four push-only entities named **Processing conversation**, **Active conversation requests**, **Last conversation duration**, and **Last conversation result**, plus a safe lifecycle event. Counts handle overlap; results distinguish completed, partial, failed, and cancelled requests without exposing conversation content. This does not measure global Hermes activity.
- Detect incomplete streaming responses rather than reporting a truncated SSE response as complete; reject explicit Hermes error/incomplete terminal metadata even when followed by `[DONE]` and sanitize server error details.

## [1.3.1] - 2026-10-04

### Fixed

- Update the last successful connection timestamp on the first successful contact and after connectivity recovers. Successful polls keep the timestamp stable, avoiding repeated Activity/Logbook state changes while other diagnostic sensors continue to update.
- Cover repeated successful polls and recovery after returned offline data or a failed coordinator refresh.

### Documentation

- Clarify that the connection timestamp records connection transitions rather than each health poll.
