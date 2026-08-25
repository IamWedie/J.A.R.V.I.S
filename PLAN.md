# JARVIS v1 — Online Desktop Assistant

Voice-controlled AI assistant with a face. Cloud brain via OpenCode Zen, all 64 models.

## Run

```
cd ~\jarvis
.\venv\Scripts\pythonw.exe app.py
```

Window opens with the reactor face. Click the mic (auto-stops on silence) or type a command.

## Stack

- UI: pywebview window + animated canvas face (idle / listening / thinking / speaking)
- Brain: Zen gateway (`https://opencode.ai/zen/v1`), default model `x-preview-f-free`
  - Switch model live from the dropdown (Claude Opus 5, GPT 5.6, Gemini, Grok, free models...)
- Ears: faster-whisper `tiny.en`, local CPU
- Mouth: edge-tts `en-US-GuyNeural`
- Tools: launch/close apps, volume, media keys, screenshots, system stats, top processes,
  file search, clipboard, window control, typing (gated), URLs, web search, lock/sleep (gated)

## Approval gate

`type_text`, `lock_screen`, `sleep_pc` show an APPROVE/DENY card in the UI before executing.
Everything else runs immediately.

## Config (.env)

| Key | Default | Notes |
|---|---|---|
| ZEN_API_KEY | (set) | from https://opencode.ai/auth |
| ZEN_MODEL | x-preview-f-free | any of the 64 model IDs |
| STT_MODEL | tiny.en | base.en / small.en = better accuracy, slower |
| TTS_VOICE | en-US-GuyNeural | any edge-tts voice |

## Files

```
app.py            entry point (window + server)
server.py         FastAPI + WebSocket event loop
core/config.py    .env settings
core/brain.py     Zen client, tool registry, approval flow
core/stt.py       microphone listener (faster-whisper)
core/tts.py       speech output (edge-tts)
core/tools/       pc_tools.py, web_tools.py
ui/               index.html, style.css, face.js, app.js
```
