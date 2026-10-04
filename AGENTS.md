# Personal Notify

Personal Android notifications: FastAPI → FCM → native Kotlin app, with optional Alexa delivery.

## Layout and commands

- `server/`: Python API, atomic JSON device state, QR pairing and optional Alexa transport.
- `android/`: native Kotlin app, Room history and WorkManager registration.
- `tests/`: offline server tests with fake upstream senders.
- `deploy/`: example Compose and Nginx configuration; substitute your domain before use.
- `AGENT_NOTIFY.md`: contract for agents that only send notifications.
- Install: `uv sync --locked` (Windows may use `scripts/uv.ps1`).
- Check: `uv run pytest -q`, `uv run ruff check server tests`, `uv run ruff format --check server tests`.
- Android without credentials: from `android/`, `./gradlew -PbuildCheck=true assembleDebug assembleDebugAndroidTest lintDebug`.

## Invariants

- Keep one server process and one worker. JSON state and pairing tickets are not multi-process safe.
- Notification history stays on the phone; do not add a server history database without agreement.
- Omitted `device` broadcasts. `urgent` defaults to true; only urgent broadcasts include configured Alexa.
- Explicit `device` sends to that phone only. Upstream acceptance does not prove device receipt.
- Do not automatically retry broadcasts: each request gets a new ID and may duplicate notifications.
- Keep credentials, FCM tokens, device state and signing material out of Git and logs.
- Preserve local `.env`, `secrets/`, Android Firebase config and unrelated edits.
- Tests must not send real FCM/Alexa messages. Deployment and real notifications need user authorization.

See `README.md` for setup and limitations, and `docs/DEPLOYMENT.md` for deployment.
