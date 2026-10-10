# Real Home Assistant runtime harness

Black-box tests against actual Home Assistant conversation, entity/device registries,
state machine, polling, activity events and unload/reload. This suite runs separately
from `tests/` (which uses module stubs); never import `tests.test_support` here.
Only the external Hermes HTTP/SSE boundary is synthetic: `runtime_tests/loopback.py`
serves aiohttp on `127.0.0.1`, not a real Hermes/model backend. Pytest disables
non-loopback sockets; the harness explicitly enables loopback and Unix sockets.
Keep the Home Assistant fixture's thread/socket teardown gates enabled; don't count
skipped gates as passes or mask thread enumeration. No live HA instance is touched.

## Isolated environments

Run both environments independently from the integration repo root. These are
**test-only** dependencies, not integration manifest requirements. `uv` may install
other transient packages required by HA/the pytest plugin. On this host the exercised
interpreters were Python 3.12.13 and 3.14.7 respectively.

| Environment | Home Assistant | pytest plugin | What partial-stream test covers |
| --- | --- | --- | --- |
| Minimum | 2024.12.5 | 0.13.195 | legacy non-ChatLog error/failed result |
| Modern | 2026.9.4 | 0.13.367 | real ChatLog partial speech retained, `partial` event |

The declared minimum in `hacs.json` is 2024.12.0; 2024.12.5 is the tested
patch release. HA 2026.9.4 requires Python >=3.14.2. The test detects real HA's
ChatLog capability and asserts the corresponding behavior (no version-based skip).
Both paths test authorization failure classification, correlation/privacy and no
retry; concurrency, cancellation and in-flight unload/reload are exercised on both.
The loopback also emits Hermes-style `finish_reason: error` and `length` terminal
frames with explicit failed/incomplete metadata before `[DONE]`, with and without
prior content. Real HA must classify them failed (no content), or partial on modern
ChatLog and failed on legacy HA (content), without a second POST or raw error details.

```bash
uv python install 3.12 3.14
uv venv "$TMPDIR/hermes-ha-runtime-2024.12" --python 3.12
uv venv "$TMPDIR/hermes-ha-runtime-2026.9" --python 3.14
uv pip install --python "$TMPDIR/hermes-ha-runtime-2024.12/bin/python" -r requirements-test-ha.txt
uv pip install --python "$TMPDIR/hermes-ha-runtime-2026.9/bin/python" -r requirements-test-ha-modern.txt

cd runtime_tests
PYTHONPATH=.. "$TMPDIR/hermes-ha-runtime-2024.12/bin/python" -m pytest -c pytest.ini
PYTHONPATH=.. "$TMPDIR/hermes-ha-runtime-2026.9/bin/python" -m pytest -c pytest.ini
```

Run the **whole** `runtime_tests/` directory from this working directory, not
only `test_activity_runtime.py`. Keep `-c pytest.ini`: running the modern pytest
without this harness configuration can hit unrelated strict fixture errors.
