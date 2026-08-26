# JARVIS v3 — Voice-Controlled AI Assistant

Holographic voice assistant with animated face, cloud brain, permanent memory, phone control, network discovery, Chromecast casting, and smart messaging.

## Quick Start

```powershell
cd ~\jarvis
.\venv\Scripts\pythonw.exe app.py
```

Click the mic or type. Wake word: "Hey Jarvis" (opt-in from UI toggle).

## Stack

- **UI**: pywebview + animated canvas face (idle / listening / thinking / speaking)
- **Brain**: OpenCode Zen gateway (`https://opencode.ai/zen/v1`), 64 models available
  - Default: `x-preview-f-free`, fallback chain: `laguna-s-2.1-free` → `big-pickle` → `nemotron-3.5-lightning-free`
  - Switch live from dropdown (Claude, GPT, Gemini, Grok, free models...)
  - Streaming: tokens arrive word-by-word, TTS starts before LLM finishes
- **Ears**: faster-whisper `tiny.en` (int8), bundled models dir, 0.7s silence detection
- **Mouth**: edge-tts `en-US-BrianNeural` at +30% speed, sentence-pipelined TTS
- **Wake word**: openwakeword `hey_jarvis` model (opt-in)
- **Voice lock**: speakeronnx `redimnet-b2`, threshold 0.55, enrollment via UI
- **Memory**: SQLite `memory.db` in `%LOCALAPPDATA%\JARVIS` — conversations, facts, network devices

## Tools (77 total)

### PC Tools
`launch_app` · `close_app` · `set_volume` · `get_volume` · `media_key` · `take_screenshot` · `system_info` · `top_processes` · `list_running_apps` · `search_files` · `get_clipboard` · `set_clipboard` · `minimize_all_windows` · `lock_screen` · `sleep_pc`

### Web Tools
`web_search` · `fetch_webpage` · `open_url` · `get_weather`

### Memory
`remember_fact` · `recall_memories`

### Network
`list_network_devices` · `where_is_device` · `net_send_message` · `net_broadcast` · `net_read_messages`

### Cast
`cast_youtube` · `cast_url` · `stop_cast` · `pause_cast` · `resume_cast` · `cast_status` · `list_cast_devices`

### Phone (via wireless ADB)
Full control: unlock, photos/selfies, camera flash, app management, calls, SMS, contacts, files, settings (WiFi/Bluetooth/airplane/brightness/volume), clipboard, notifications, reboot, media playback.

### Phone Agent
Autonomous multi-step tasks. Smart action table for known tasks, vision pipeline for unknown. Waits for phone inactivity before acting. Checks lock state before unlocking.

## Network Messaging

Smart router: accepts device name ("my TV", "my phone") or IP, auto-routes via:
1. ADB notification (Android phone)
2. Chromecast cast (Google TV — styled message page)
3. HTTP POST (other JARVIS instances on port 9998)
4. `msg.exe` broadcast (Windows PCs)

## Network Discovery

Ping sweep + ARP table + mDNS (zeroconf) + SSDP + OUI vendor DB (30+ vendors). SQLite device cache. Device types auto-detected: phone, TV, laptop, PC.

## Phone Setup

- Model: Honor LLY-LX2, Android 14, 1080x2412
- Connect: wireless ADB at `192.168.1.6`, paired with `adb pair`
- ADB binary: `platform-tools/adb.exe` (gitignored)

## Config (.env)

| Key | Default | Notes |
|---|---|---|
| ZEN_API_KEY | (set) | from https://opencode.ai/auth |
| ZEN_BASE_URL | https://opencode.ai/zen/v1 | OpenAI-compatible endpoint |
| ZEN_MODEL | x-preview-f-free | any of the 64 model IDs |
| STT_MODEL | tiny.en | base.en / small.en = better accuracy, slower |
| TTS_VOICE | en-US-BrianNeural | any edge-tts voice |
| TTS_RATE | +30% | speech speed |
| WAKE_ENABLED_DEFAULT | false | start with wake word active |

## Approval Gate

`type_text`, `lock_screen`, `sleep_pc` show an APPROVE/DENY card in the UI before executing.
Everything else runs immediately.

## File Structure

```
app.py                  entry point (window + server)
server.py               FastAPI + WebSocket event loop
core/
  config.py             .env loading
  brain.py              LLM client, streaming, 77 tools, system prompt, phone agent
  stt.py                faster-whisper STT with bundled models
  tts.py                edge-tts sentence-pipelined TTS
  memory.py             SQLite: conversations, facts, devices
  mic.py                microphone listener, wake word integration
  voiceid.py            voice enrollment / ID
  greeter.py            greeting phrases
  netdiscovery.py       ping sweep, ARP, mDNS, SSDP, OUI vendors
  tools/
    pc_tools.py         PC action tools
    web_tools.py        web search, fetch, weather
core/net/
  adb_controller.py     wireless ADB phone control (~870 lines)
  cast_controller.py    Chromecast via pychromecast
  agent.py              autonomous phone agent with vision
  netmsg.py             network messaging (HTTP + smart router)
ui/                     index.html, style.css, face.js, app.js
platform-tools/         ADB binaries (gitignored)
models/                 bundled whisper models (gitignored)
```

## Roadmap

### Phase A — Core
- [x] Smart message router (device names → ADB/Cast/HTTP)
- [x] Selfie end-to-end verification (correct switch coords, state tracking, selfie_verify())
- [x] PLAN.md rewrite

### Phase B — Feel Alive
- [x] Barge-in: mic hot during speech, stops JARVIS on user voice
- [x] Conversation mode: 60s follow-up window, no wake word
- [x] Screen awareness: "what am I looking at" → OCR → spoken
- [x] Reminders/alarms: background scheduler → voice + phone notification

### Phase C — Depth
- [x] Derja prep: multilingual whisper STT (auto-detect), Hedi + Reem voices, language-matching prompt
- [x] Auto-learning: pattern-based fact extraction, topic upserts, corrections overwrite
- [x] Per-user voice ID: multi-profile identify(), attributed memories/facts, legacy migration
- [x] Telegram bot brain: long-polling, owner-lock, telegram_notify tool, reminder relay
- [ ] Local vision: deferred to Phase D (moondream2 downloaded, config disabled)
- [x] UI automation depth (launch_app, close_app, volume, media, focus, screenshot, OCR)

### Phase D — Polish
- [ ] Morning briefing (weather/calendar/news)
- [ ] Crash watchdog + auto-restart
