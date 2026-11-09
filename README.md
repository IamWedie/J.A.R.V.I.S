# J.A.R.V.I.S

A voice-controlled personal assistant for Windows, inspired by Iron Man.

## Features

- **Voice**: Wake word ("Hey Jarvis"), voice lock (only your voice), bilingual STT (English + Arabic TN)
- **Brain**: Cloud AI via [OpenCode Zen](https://opencode.ai/zen) — 60+ models, free tier included
- **Tools**: Launch/close apps, volume, media keys, screenshots, OCR, screen describe, system reports, file search, clipboard, web search, approval-gated power actions
- **Security**: Token-authenticated local UI, signed tool manifest, default-deny capability policy with revocable grants, tamper-evident audit log — see [SECURITY.md](SECURITY.md)
- **Memory**: Permanent local SQLite — conversations, auto-learned facts, per-user profiles
- **Smart Reminders**: Voice-scheduled reminders with notifications
- **Barge-in**: Interrupt JARVIS while speaking
- **Auto-learning**: Extracts and remembers facts from conversation
- **Morning Briefing**: Weather, news, reminders on startup
- **Animated Face**: Canvas-rendered with eye tracking, blink, state-dependent colors

Phone control (ADB), Chromecast, network messaging, and the Telegram bot are disabled by default and must not be assumed available.

## System Requirements

- Windows 10/11
- Python 3.12+
- Microphone + speakers
- Internet connection (for AI brain, TTS, web search)

## Quick Start

```bash
pip install -r requirements.txt
python app.py
```

First run shows a five-step setup wizard (the personal build has **no license step**):
1. Accept Terms of Use
2. Enter your Zen API key (free at opencode.ai/auth)
3. System check (mic, speakers, internet)
4. Pick a brain model
5. Optional: Enroll your voice for voice lock

## Install (Packaged)

```bash
pyinstaller jarvis.spec
```

Then compile `installer.iss` with [Inno Setup](https://jrsoftware.org/isinfo.php) to create `JARVIS_setup.exe`.

## Configuration

All settings are in `.env` (created by the setup wizard or installer):

| Variable | Default | Description |
|---|---|---|
| `ZEN_API_KEY` | — | Your OpenCode Zen API key |
| `ZEN_MODEL` | `x-preview-f-free` | Brain model |
| `STT_MODEL` | `tiny` | Whisper model (tiny = fast) |
| `STT_LANG` | `auto` | Auto-detect, filtered to en/ar |
| `TTS_VOICE` | `ar-TN-HediNeural` | TTS voice |
| `TTS_RATE` | `+0%` | Speech speed |
| `WAKE_ENABLED` | `1` | Wake word on/off |
| `JARVIS_PIN_MIN_LENGTH` | `6` | Minimum approval PIN length |

Phone (`PHONE_*` / `PHONE_PIN`), Telegram (`TELEGRAM_*`), and license
(`LICENSE_*`) variables still work when the corresponding feature is
explicitly enabled in `core/security/features.py`; those features are
**disabled by default** and their secrets are not treated as supported
configuration for a personal build.

## Security

- Every HTTP/API route requires a per-launch bearer token (`X-JARVIS-Token`); WebSocket commands only run after token authentication.
- Tools are governed by a signed tool manifest (Ed25519 signature + per-file SHA-256 checks) and a **default-deny** capability policy with scoped, revocable grants.
- Approvals are request-bound, single-use, and session-owned; sensitive/critical actions are UI-only.
- Every tool decision is written to a hash-chained, HMAC-bound audit log; `scripts/audit_verify.py`, `scripts/audit_export.py`, and `scripts/audit_reset.py` operate on it.
- Details, threat boundary, key custody, and data locations: [SECURITY.md](SECURITY.md).

## Privacy

- API key and voice fingerprint never leave your PC
- Memory stored locally in SQLite — nothing in the cloud
- Speech transcribed locally (faster-whisper)
- AI replies generated in the cloud (OpenCode Zen)
- `.env` is gitignored — secrets never committed

## License

Proprietary. All rights reserved.
