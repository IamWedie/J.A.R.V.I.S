# Changelog

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
