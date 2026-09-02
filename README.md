# J.A.R.V.I.S

A voice-controlled personal assistant for Windows, inspired by Iron Man.

## Features

- **Voice**: Wake word ("Hey Jarvis"), voice lock (only your voice), bilingual STT (English + Arabic TN)
- **Brain**: Cloud AI via [OpenCode Zen](https://opencode.ai/zen) — 60+ models, free tier included
- **Tools**: Launch/close apps, volume, media keys, screenshots, OCR, screen describe, system reports, file search, clipboard, web search, approval-gated power actions
- **Phone Control**: Wireless ADB — take selfies, open apps, notifications, volume, screen control
- **Chromecast**: Cast URLs, YouTube, Netflix, Spotify to TV
- **Network Messaging**: Send messages between PC and phone
- **Memory**: Permanent local SQLite — conversations, auto-learned facts, per-user profiles
- **Telegram Bot**: Send commands and alerts via Telegram
- **Smart Reminders**: Voice-scheduled reminders with notifications
- **Barge-in**: Interrupt JARVIS while speaking
- **Auto-learning**: Extracts and remembers facts from conversation
- **Morning Briefing**: Weather, news, reminders on startup
- **Animated Face**: Canvas-rendered with eye tracking, blink, state-dependent colors

## System Requirements

- Windows 10/11
- Python 3.12+
- Microphone + speakers
- Internet connection (for AI brain, TTS, web search)
- Optional: Android phone with wireless ADB for phone control
- Optional: Chromecast device for TV casting

## Quick Start

```bash
pip install -r requirements.txt
python app.py
```

First run shows a setup wizard:
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
| `PHONE_PIN` | — | Phone unlock PIN |
| `TELEGRAM_BOT_TOKEN` | — | Telegram bot token |
| `TELEGRAM_CHAT_ID` | — | Your Telegram chat ID |
| `PHONE_ADDR` | — | Phone's private VPN address (e.g. `100.x.y.z`) — JARVIS connects here only |
| `PHONE_SERIAL` | — | Enrolled phone serial (identity whitelist) |
| `LICENSE_KEY` | — | License key (set by the installer wizard / onboarding) to activate JARVIS |
| `LICENSE_SERVER_URL` | — | Optional license-server base URL for online validate/activate/revoke |
| `JARVIS_PIN_MIN_LENGTH` | `6` | Minimum approval PIN length |

## Privacy

- API key and voice fingerprint never leave your PC
- Memory stored locally in SQLite — nothing in the cloud
- Speech transcribed locally (faster-whisper)
- AI replies generated in the cloud (OpenCode Zen)
- `.env` is gitignored — secrets never committed

## License

Proprietary. All rights reserved.
