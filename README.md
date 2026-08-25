# J.A.R.V.I.S

A voice-controlled personal assistant for Windows, inspired by Iron Man.

- Wake word: say "Hey Jarvis"
- Voice lock: only your voice can command it (speaker verification, enrolled locally)
- Cloud brain via [OpenCode Zen](https://opencode.ai/zen) (your own API key, 60+ models, free models included)
- Speech-to-text locally (faster-whisper), neural TTS voices (edge-tts)
- Tools: launch/close apps, volume, media keys, screenshots, system reports,
  file search, clipboard, window control, web search, approval-gated power actions
- Permanent local memory (SQLite) - nothing stored in the cloud
- System tray background mode, optional startup sound

## Run (development)

```
pip install -r requirements.txt
python app.py
```

First run shows a setup wizard: accept terms, paste your Zen API key, pick a model, enroll your voice.

## Install (packaged)

Build with `pyinstaller jarvis.spec`, then compile `installer.iss` with Inno Setup.

## Privacy

- Your API key and voice fingerprint never leave your PC (the key is only sent to opencode.ai for AI requests)
- Memory/conversation history is stored locally in SQLite
- Spoken commands are transcribed locally; AI replies are generated in the cloud
