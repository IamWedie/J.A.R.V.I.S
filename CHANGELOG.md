# Changelog

## 3.0.0 (2026-11-08)

### Secure Core

- **Authenticated HTTP/WS boundary**: per-launch bearer token enforced by middleware on every `/api/*` route (except `/api/health`); WebSocket commands only execute after `{cmd:"auth"}` token authentication; origin checked; single active control session with approval cancellation on replace.
- **Ed25519-signed tool manifest** with per-file SHA-256 code checks; frozen builds fail closed on an unverifiable manifest; `scripts/verify_tool_manifest.py` CI gate (`31 tool(s)`).
- **Default-deny capability policy** with manifest-bounded maximums, HMAC-envelope integrity (corrupt policy denies everything), and scoped, expiring grants revocable from the settings UI (`/api/security/grants`, `/api/security/grants/{id}/revoke`, `policy.change` audited with `decision="grant"|"revoke"`).
- **Request-bound approvals**: random request id, single-use, session-owned, expiring; voice approval only for guarded actions with an enrolled voice match; sensitive/critical are UI-only.
- **Isolated execution lanes**: restricted/egress lanes under Windows Job Objects; privileged host lane via anonymous pipe (not sandboxed).
- **Tamper-evident audit log**: SQLite hash chain with HMAC-bound tips, redacted arguments, 90-day startup retention with re-anchoring prune; read-only verification (`scripts/audit_verify.py`), NDJSON export (`scripts/audit_export.py`), and archive-and-reset (`scripts/audit_reset.py`, `/api/security/audit_reset`) sharing one helper (`core/security/audit_maintenance.py`) that never deletes archives and writes `audit_recovery.json`; audit-locked state recoverable from Settings.
- **Disabled by default**: phone/ADB, Chromecast cast, LAN messaging, Telegram bot, license server client, and auto-update are off; their optional modules are not imported and no extra port opens.
- **Personal setup wizard**: removed the license step (`wizStep0`); the UI ignores `license_needed`; language switching now applies translations live (no page reload) with English fallback/restoration.
- **Settings security panel**: live security status, active grants with revoke, recent security events, and a double-confirmed archive & reset button visible only when audit-locked.
- **ADB argument hardening** for the disabled phone path: every model-reachable ADB argument is validated or quoted.
- **CI**: `security.yml` workflow runs compileall, manifest verification, and the full test suite on Windows.

## 2.3.0 (2026-09-02)

### Asymmetric License System
- **Ed25519 signed keys** (`core/license_keys.py`): license keys now embed a 64-byte Ed25519 signature over their payload, verified **offline** by the app using only the baked-in public keyring (`core/pubkeys.json`). No signing secret ships in the app.
- **Key rotation**: the app accepts a *ring* of active public keys, so you can rotate the signing private key without breaking previously-issued keys (`scripts/rotate_key.py`).
- **Removed `LICENSE_SECRET`**: online/offline distribution no longer depends on a symmetric secret; `core/vault.py` no longer treats it as a secret you must ship.
- **License server** (`license_server/`): local/self-hosted FastAPI service with `POST /license/{validate,activate,deactivate}` and `POST /admin/mint`, backed by SQLite. Enforces an activation limit per key (anti-sharing) and central revocations.
- **Installer wizard**: now collects the Zen API key, a strong approval PIN, and the license key; writes them into `config.env`, and the app auto-activates the license on first launch (`server.py::_auto_activate_license`).
- **Offline verification preserved**: keys are verifiable fully offline; online server check is layered on when `LICENSE_SERVER_URL` is configured.

## 2.2.0 (2026-08-29)

### Security & Paid Distribution
- **Phone transport now VPN-only**: JARVIS connects to the phone ONLY via a configured private VPN address (`PHONE_ADDR`), never scanning the LAN. Removes wireless-debugging exposure on public networks.
- **Serial whitelist** (`PHONE_SERIAL`): connected device must match your enrolled phone serial or it disconnects/refuses, on top of ADB crypto pairing.
- **PIPN hardening**: strong `JARVIS_PIN` validation (min length + common-pin blocklist), per-source brute-force lockout on `/pin` (max attempts + cooldown), weak-PIN startup warning.
- **License system** (`core/license.py`): HMAC-signed offline license keys (`JARV-XXXXX-XXXXX-XXXXX`), check-digit typo detection, activation stored in the data dir, onboarding license-step in the setup wizard.
- **Onboarding**: license activation step added before terms/API-key in the first-run wizard.
- TERMS.md rewritten cleanly (UTF-8 BOM) with EULA/license terms.

## 2.1.0 (2026-08-26)

### Store-Readiness Overhaul
- **UI**: Added ARIA accessibility labels, keyboard navigation, toast notification system, loading spinners, responsive design (480px/768px/1200px breakpoints), Rajdhani font loading, viewport meta tag
- **Onboarding**: Added system requirements check (mic, speakers, internet) to setup wizard
- **Packaging**: Pinned all 28 dependencies with exact versions, added VERSION file for installer, updated PyInstaller spec to bundle platform-tools
- **Performance**: SQLite connection pooling (thread-local), message log pruning (max 200), WebSocket exponential backoff (1s→30s), VACUUM on memory wipe
- **Network**: Added /api/health endpoint, offline banner with 30s polling, graceful degradation
- **Crash Recovery**: Watchdog now works with packaged builds (detects frozen vs dev), auto-detects executable path
- **Memory/DB**: Schema versioning with migration system (schema_meta table), version tracking
- **Security**: Moved ADB PIN to .env config, added CORS middleware to FastAPI, PHONE_PIN configurable
- **Documentation**: Expanded README with full feature list, system requirements, configuration table, privacy section

### Previous Features
- Barge-in: mic hot during speech, stops JARVIS on user voice
- Conversation mode: 60s follow-up window
- Screen awareness: OCR + describe_screen
- Reminders/alarms with Telegram notifications
- Derja STT: multilingual whisper tiny, auto-detect filtered to en/ar
- Auto-learning: fact extraction + upsert
- Per-user voice ID enrollment
- Telegram bot with long-polling
- Morning briefing: Soliman weather + Tunisia news
- Camera switch button fix (950, 2100)
- Crash watchdog with auto-restart

## 2.0.0 (2026-08-20)

- Initial voice assistant with animated face
- Voice lock, wake word, memory, tools
- Phone control via ADB
- Chromecast support
