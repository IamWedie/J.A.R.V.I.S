import asyncio
import json
import os
import time

import numpy as np
import sounddevice as sd
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import core.config as config
from core.brain import Brain, FRIENDLY_NAMES, is_free_model
from core.mic import mic, SAMPLE_RATE
from core.stt import transcriber
from core.tts import speaker
from core.voiceid import voiceid
from core.net import netmsg

from datetime import datetime
from core.logging_setup import get_logger

log = get_logger("server")

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:8741", "http://localhost:8741"],
    allow_methods=["*"],
    allow_headers=["*"],
)

UI_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui")

clients = set()
brain = Brain()
processing_lock = asyncio.Lock()
current_state = "idle"
main_loop = None
listening_paused = False
barge_in_enabled = True
_barge_in_event = asyncio.Event()
_barge_in_text = None

SILENCE_THRESHOLD = 0.008
SILENCE_DURATION = 0.7
MAX_RECORD_SECONDS = 12

_chime_audio = None


def _build_chime():
    sr = SAMPLE_RATE
    t1 = np.linspace(0, 0.18, int(sr * 0.18), False)
    t2 = np.linspace(0, 0.22, int(sr * 0.22), False)
    tone1 = np.sin(880 * t1 * np.pi * 2) * np.exp(-t1 * 9) * 0.35
    tone2 = np.sin(1320 * t2 * np.pi * 2) * np.exp(-t2 * 8) * 0.30
    gap = np.zeros(int(sr * 0.03))
    return np.concatenate([tone1, gap, tone2]).astype(np.float32)


async def play_chime():
    global _chime_audio
    if _chime_audio is None:
        _chime_audio = _build_chime()
    audio = _chime_audio

    def play():
        try:
            sd.play(audio, SAMPLE_RATE)
            sd.wait()
        except Exception:
            pass

    await asyncio.get_running_loop().run_in_executor(None, play)


@app.get("/")
async def index():
    return FileResponse(os.path.join(UI_DIR, "index.html"), headers=NO_CACHE)


@app.get("/style.css")
async def style():
    return FileResponse(os.path.join(UI_DIR, "style.css"), media_type="text/css", headers=NO_CACHE)


@app.get("/face.js")
async def facejs():
    return FileResponse(os.path.join(UI_DIR, "face.js"), media_type="application/javascript", headers=NO_CACHE)


@app.get("/app.js")
async def appjs():
    return FileResponse(os.path.join(UI_DIR, "app.js"), media_type="application/javascript", headers=NO_CACHE)


NO_CACHE = {"Cache-Control": "no-store"}


@app.get("/favicon.ico")
async def favicon():
    ico = os.path.join(os.path.dirname(os.path.abspath(__file__)), "jarvis.ico")
    if os.path.exists(ico):
        return FileResponse(ico, media_type="image/x-icon")
    return FileResponse(os.path.join(UI_DIR, "favicon.ico"), media_type="image/x-icon")


async def send_event(event):
    dead = []
    data = json.dumps(event)
    for ws in list(clients):
        try:
            await ws.send_text(data)
        except Exception:
            dead.append(ws)
    for ws in dead:
        clients.discard(ws)


async def set_state(value):
    global current_state
    current_state = value
    await send_event({"type": "state", "value": value})
    update_wake_arm()


def update_wake_arm():
    eligible = (
        mic.wake_enabled
        and not listening_paused
        and current_state == "idle"
        and not processing_lock.locked()
        and bool(config.ZEN_API_KEY)
    )
    mic.set_armed(eligible)


class ApprovalIn(BaseModel):
    approved: bool


class ModelIn(BaseModel):
    model: str


@app.post("/api/approve")
async def api_approve(body: ApprovalIn):
    brain.resolve_approval(body.approved)
    return {"ok": True}


@app.post("/api/model")
async def api_model(body: ModelIn):
    brain.model = body.model
    return {"ok": True}


@app.get("/api/models")
async def api_models():
    try:
        models = await brain.fetch_models()
        return {"models": models}
    except Exception as e:
        return {"models": [], "error": str(e)}


class SetupIn(BaseModel):
    key: str


@app.get("/api/setup_status")
async def api_setup_status():
    return {"setup_needed": not bool(config.ZEN_API_KEY)}


@app.post("/api/setup_validate")
async def api_setup_validate(body: SetupIn):
    from openai import AsyncOpenAI
    client = AsyncOpenAI(base_url=config.ZEN_BASE_URL, api_key=body.key.strip(), timeout=30)
    try:
        response = await client.models.list()
    except Exception as e:
        return {"ok": False, "error": f"Key rejected: {str(e)[:120]}"}
    models = []
    for m in response.data:
        mid = m.id
        free = is_free_model(mid)
        label = FRIENDLY_NAMES.get(mid) or mid
        if free:
            label += "  •FREE"
        models.append({"id": mid, "label": label, "free": free})
    models.sort(key=lambda x: (not x["free"], x["label"].lower()))
    return {"ok": True, "models": models}


class SetupCompleteIn(BaseModel):
    key: str
    model: str


@app.post("/api/setup_complete")
async def api_setup_complete(body: SetupCompleteIn):
    config.save_settings({"ZEN_API_KEY": body.key.strip(), "ZEN_MODEL": body.model.strip()})
    config.ZEN_API_KEY = body.key.strip()
    config.DEFAULT_MODEL = body.model.strip()
    brain.reset_client()
    return {"ok": True}


@app.get("/api/terms")
async def api_terms():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "TERMS.md")
    with open(path, "r", encoding="utf-8") as f:
        return {"text": f.read()}


@app.get("/api/health")
async def api_health():
    online = False
    try:
        import urllib.request
        urllib.request.urlopen("https://www.google.com", timeout=3)
        online = True
    except Exception:
        pass
    from core.i18n import get_language
    return {"online": online, "version": config.VERSION, "language": get_language()}


@app.get("/api/language")
async def api_language():
    from core.i18n import get_available_languages, get_language
    return {"current": get_language(), "available": get_available_languages()}


@app.get("/api/system_check")
async def api_system_check():
    checks = {}
    try:
        import sounddevice as sd
        devs = sd.query_devices()
        has_mic = any(d["max_input_channels"] > 0 for d in devs)
        has_speaker = any(d["max_output_channels"] > 0 for d in devs)
        checks["microphone"] = has_mic
        checks["speaker"] = has_speaker
    except Exception:
        checks["microphone"] = False
        checks["speaker"] = False
    try:
        import urllib.request
        urllib.request.urlopen("https://www.google.com", timeout=3)
        checks["internet"] = True
    except Exception:
        checks["internet"] = False
    checks["voice_engine"] = True
    checks["stt_model"] = bool(config.STT_MODEL)
    return checks


@app.get("/api/memory_stats")
async def api_memory_stats():
    from core import memory
    return memory.stats()


async def think_and_speak(user_text, speaker_name=None, source="ui"):
    global _barge_in_text
    await set_state("thinking")
    if barge_in_enabled:
        mic.start_stream()
    text_q = asyncio.Queue()
    speak_task = asyncio.create_task(speaker.speak_from_queue(text_q))
    parts = []
    _barge_in_event.clear()
    _barge_in_text = None
    barge_task = asyncio.create_task(_barge_in_detector())

    async def on_chunk(chunk):
        parts.append(chunk)
        await send_event({"type": "reply_chunk", "text": chunk})
        await text_q.put(chunk)
        if current_state == "thinking":
            asyncio.create_task(set_state("speaking"))

    try:
        reply = await brain.ask(user_text, on_chunk=on_chunk, speaker=speaker_name, source="voice")
    except Exception as e:
        await send_event({"type": "error", "message": str(e)})
        reply = "Sorry sir, something went wrong."
    if reply and not parts:
        await text_q.put(reply)
    await text_q.put(None)
    full_reply = "".join(parts) or reply
    await send_event({"type": "reply", "text": full_reply})
    await speak_task
    barge_task.cancel()
    try:
        await barge_task
    except asyncio.CancelledError:
        pass

    try:
        from core import memory
        memory.auto_learn(user_text, full_reply, user=speaker_name or "")
    except Exception:
        pass

    if _barge_in_text:
        interrupted_text, barge_speaker = _barge_in_text
        _barge_in_text = None
        log.info(f"[barge] processing interrupted input: {interrupted_text}")
        await send_event({"type": "user_said", "text": interrupted_text})
        await think_and_speak(interrupted_text, speaker_name=barge_speaker)
        return

    await set_state("idle")


async def capture_command():
    mic.begin_command_capture()
    heard_sound = False
    silent_since = None
    started = time.time()
    while mic.mode == "command":
        await asyncio.sleep(0.08)
        if mic.stop_requested:
            break
        level = mic.level
        if level > SILENCE_THRESHOLD:
            heard_sound = True
            silent_since = None
        elif heard_sound:
            if silent_since is None:
                silent_since = time.time()
            elif time.time() - silent_since > SILENCE_DURATION:
                break
        if time.time() - started > MAX_RECORD_SECONDS:
            break
    audio = await asyncio.to_thread(mic.end_command_capture)
    if mic.stop_requested and not heard_sound:
        return None
    return audio


async def voice_pipeline(play_intro):
    async with processing_lock:
        speaker.stop()
        await set_state("listening")
        chime_task = asyncio.create_task(play_chime()) if play_intro else None
        audio = await capture_command()
        if chime_task:
            try:
                await chime_task
            except Exception:
                pass

        if audio is None or len(audio) / SAMPLE_RATE < 0.6:
            await set_state("idle")
            return

        from core.voiceid import voiceid as _vid
        speaker_name = None
        if _vid.enrolled:
            speaker_name = await asyncio.to_thread(_vid.identify, audio)
            if speaker_name is None:
                await send_event({"type": "voice_rejected"})
                try:
                    from core import memory
                    memory.log("event", "[unrecognized voice ignored]")
                except Exception:
                    pass
                await set_state("idle")
                return

        user_text = await asyncio.to_thread(transcriber.transcribe_array, audio)
        if not user_text:
            await set_state("idle")
            return

        await send_event({"type": "user_said", "text": user_text})
        await think_and_speak(user_text, speaker_name=speaker_name, source="voice")
        asyncio.create_task(follow_up_window())
    update_wake_arm()


async def text_flow(user_text):
    if current_state != "idle":
        return
    await send_event({"type": "user_said", "text": user_text})
    async with processing_lock:
        await think_and_speak(user_text)
    update_wake_arm()


CONVERSATION_FOLLOWUP = getattr(config, "CONVERSATION_TIMEOUT", 60)


async def follow_up_window():
    if mic.wake_enabled:
        return
    if CONVERSATION_FOLLOWUP <= 0:
        return
    mic.start_stream()
    started = time.time()
    silent_since = None
    while time.time() - started < CONVERSATION_FOLLOWUP:
        await asyncio.sleep(0.08)
        if current_state != "idle":
            return
        if mic.level > SILENCE_THRESHOLD:
            silent_since = None
        else:
            if silent_since is None:
                silent_since = time.time()
            elif time.time() - silent_since > 2.0:
                break
    if current_state != "idle":
        return
    mic.begin_command_capture()
    heard_speech = False
    started = time.time()
    silent_since = None
    while time.time() - started < MAX_RECORD_SECONDS:
        await asyncio.sleep(0.08)
        if mic.stop_requested:
            break
        if mic.level > SILENCE_THRESHOLD:
            heard_speech = True
            silent_since = None
        elif heard_speech:
            if silent_since is None:
                silent_since = time.time()
            elif time.time() - silent_since > SILENCE_DURATION:
                break
    audio = await asyncio.to_thread(mic.end_command_capture)
    if not audio or len(audio) / SAMPLE_RATE < 0.5:
        return
    from core.voiceid import voiceid as _vid
    fu_speaker = None
    if _vid.enrolled:
        fu_speaker = await asyncio.to_thread(_vid.identify, audio)
        if fu_speaker is None:
            return
    user_text = await asyncio.to_thread(transcriber.transcribe_array, audio)
    if not user_text:
        return
    await send_event({"type": "user_said", "text": user_text})
    await send_event({"type": "state", "value": "listening"})
    async with processing_lock:
        await think_and_speak(user_text, speaker_name=fu_speaker)
    update_wake_arm()


async def _barge_in_detector():
    global _barge_in_text
    barge_start = None
    while True:
        await asyncio.sleep(0.06)
        if not barge_in_enabled:
            barge_start = None
            continue
        if not speaker.speaking:
            barge_start = None
            continue
        if current_state not in ("speaking", "thinking"):
            barge_start = None
            continue
        if mic.level > SILENCE_THRESHOLD * 1.5:
            if barge_start is None:
                barge_start = time.time()
            elif time.time() - barge_start > 0.2:
                log.info("[barge] voice detected during speech — stopping")
                speaker.stop()
                _barge_in_event.set()
                mic.begin_command_capture()
                barge_start = None
                await asyncio.sleep(0.08)
                audio = await asyncio.to_thread(mic.end_command_capture)
                if audio is not None and len(audio) / SAMPLE_RATE >= 0.5:
                    from core.voiceid import voiceid as _vid
                    barge_speaker = None
                    if _vid.enrolled:
                        barge_speaker = await asyncio.to_thread(_vid.identify, audio)
                        if barge_speaker is None:
                            _barge_in_text = None
                            continue
                    text = await asyncio.to_thread(transcriber.transcribe_array, audio)
                    if text:
                        _barge_in_text = (text, barge_speaker)
                break
        else:
            barge_start = None


def _on_wake_from_audio_thread():
    if main_loop is None:
        return

    def schedule():
        asyncio.create_task(safe_wake_pipeline())

    try:
        main_loop.call_soon_threadsafe(schedule)
    except RuntimeError:
        pass


async def safe_wake_pipeline():
    try:
        if current_state != "idle" or processing_lock.locked():
            return
        await voice_pipeline(play_intro=True)
    except Exception as e:
        await send_event({"type": "error", "message": str(e)})
        await set_state("idle")


async def level_broadcaster():
    while True:
        if clients:
            await send_event({"type": "level", "value": round(min(mic.level * 6, 1.0), 3)})
        await asyncio.sleep(0.09)


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    clients.add(ws)
    await ws.send_text(json.dumps({"type": "state", "value": current_state}))
    await ws.send_text(json.dumps({"type": "wake", "enabled": mic.wake_enabled}))
    await ws.send_text(json.dumps({"type": "voice_status", "enrolled": voiceid.enrolled}))
    try:
        from core import memory
        for item in memory.recent_conversations(50):
            await ws.send_text(json.dumps({
                "type": "user_said" if item["role"] == "user" else "reply",
                "text": item["text"],
            }))
    except Exception as e:
        log.warning(f"history send failed: {e}")
    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            cmd = msg.get("cmd")
            if cmd == "listen":
                if current_state == "listening" and mic.mode == "command":
                    mic.request_stop()
                else:
                    asyncio.create_task(safe_voice_pipeline())
            elif cmd == "abort":
                mic.end_command_capture()
                speaker.stop()
            elif cmd == "chat":
                text = (msg.get("text") or "").strip()
                if text:
                    asyncio.create_task(text_flow(text))
            elif cmd == "approval":
                log.info("WS: approval command received: %s", msg.get("approved"))
                brain.resolve_approval(bool(msg.get("approved")))
            elif cmd == "model":
                brain.model = msg.get("model") or brain.model
            elif cmd == "reset":
                brain.reset_history()
                await send_event({"type": "cleared"})
            elif cmd == "wake_toggle":
                try:
                    enabled = bool(msg.get("enabled"))
                    if enabled:
                        mic.enable_wake()
                    else:
                        mic.disable_wake()
                    update_wake_arm()
                    config.save_settings({"WAKE_ENABLED": "1" if enabled else "0"})
                    await send_event({"type": "wake", "enabled": mic.wake_enabled})
                except Exception as e:
                    log.warning(f"wake toggle failed: {e}", exc_info=True)
                    await send_event({"type": "error", "message": f"Wake toggle failed: {e}"} )
            elif cmd == "tts_settings":
                try:
                    voice = msg.get("voice") or config.TTS_VOICE
                    rate = msg.get("rate") or config.TTS_RATE
                    config.TTS_VOICE = voice
                    config.TTS_RATE = rate
                    speaker.rate = None
                    config.save_settings({"TTS_VOICE": voice, "TTS_RATE": rate})
                    await send_event({"type": "tts_saved", "voice": voice, "rate": rate})
                except Exception as e:
                    await send_event({"type": "error", "message": f"Voice settings failed: {e}"})
            elif cmd == "tts_test":
                asyncio.create_task(test_voice())
            elif cmd == "enroll":
                asyncio.create_task(safe_enroll_flow())
            elif cmd == "voice_reset":
                voiceid.reset()
                await send_event({"type": "voice_status", "enrolled": False})
            elif cmd == "wipe_memory":
                try:
                    from core import memory
                    memory.wipe_memory()
                    brain.reset_history()
                    await send_event({"type": "cleared"})
                    await send_event({"type": "memory_wiped"})
                except Exception as e:
                    await send_event({"type": "error", "message": f"Wipe failed: {e}"})
            elif cmd == "update_download":
                async def _do_update():
                    from core.updater import check_for_update, download_update, apply_zip_update
                    await send_event({"type": "update_progress", "status": "checking"})
                    info = await asyncio.to_thread(check_for_update)
                    if not info:
                        await send_event({"type": "error", "message": "No update available"})
                        return
                    await send_event({"type": "update_progress", "status": "downloading"})
                    path = await asyncio.to_thread(download_update, info)
                    if not path:
                        await send_event({"type": "error", "message": "Download failed"})
                        return
                    if info.get("download_url", "").endswith(".zip"):
                        ok = await asyncio.to_thread(apply_zip_update, info)
                    else:
                        from core.updater import install_update
                        ok = await asyncio.to_thread(install_update, path)
                    if ok:
                        await send_event({"type": "update_progress", "status": "ready"})
                        speaker.stop()
                        await speaker.speak("Update downloaded. JARVIS will restart to apply it.")
                    else:
                        await send_event({"type": "error", "message": "Install failed"})
                asyncio.create_task(_do_update())
            elif cmd == "set_language":
                lang = msg.get("lang", "en")
                from core.i18n import set_language
                set_language(lang)
                config.save_settings({"UI_LANG": lang})
                await send_event({"type": "language_changed", "lang": lang})
    except WebSocketDisconnect:
        pass
    finally:
        clients.discard(ws)


async def safe_voice_pipeline():
    try:
        if current_state != "idle" or processing_lock.locked():
            return
        await voice_pipeline(play_intro=True)
    except Exception as e:
        await send_event({"type": "error", "message": str(e)})
        await set_state("idle")


enroll_lock = asyncio.Lock()

ENROLL_PHRASES = [
    "Hey Jarvis, what time is it?",
    "Jarvis, open Spotify and play some music",
    "Hey Jarvis, how is the weather today?",
]


async def _capture_for_enroll(phrase):
    for attempt in range(3):
        await asyncio.sleep(0.6)
        audio = await capture_command()
        if audio is not None and len(audio) / SAMPLE_RATE >= 1.5:
            return audio
        await send_event({
            "type": "enroll_prompt",
            "index": 0,
            "total": len(ENROLL_PHRASES),
            "phrase": phrase,
            "retry": True,
        })
    return None


async def enroll_flow():
    async with enroll_lock:
        if processing_lock.locked():
            await send_event({"type": "error", "message": "Busy right now, try again in a moment."})
            return
        samples = []
        total = len(ENROLL_PHRASES)
        for i, phrase in enumerate(ENROLL_PHRASES, 1):
            await set_state("listening")
            await send_event({"type": "enroll_prompt", "index": i, "total": total, "phrase": phrase})
            await play_chime()
            audio = await _capture_for_enroll(phrase)
            if audio is None:
                await send_event({"type": "error", "message": "Enrollment cancelled - no voice captured."})
                await set_state("idle")
                return
            samples.append(audio)
            await send_event({"type": "phrase_done", "index": i})
        await set_state("thinking")
        try:
            ok = await asyncio.to_thread(voiceid.enroll, samples)
        except Exception as e:
            await send_event({"type": "error", "message": f"Enrollment failed: {e}"})
            await set_state("idle")
            return
        if ok:
            await send_event({"type": "enroll_done"})
            await send_event({"type": "voice_status", "enrolled": True})
            await speaker.speak("Voice print saved. From now on, I only obey this voice.")
        await set_state("idle")


async def safe_enroll_flow():
    try:
        await enroll_flow()
    except Exception as e:
        log.warning(f"enroll flow failed: {e}", exc_info=True)
        await send_event({"type": "error", "message": str(e)})
        await set_state("idle")


async def test_voice():
    if processing_lock.locked():
        await send_event({"type": "error", "message": "Busy right now."})
        return
    async with processing_lock:
        try:
            await set_state("speaking")
            await speaker.speak("Voice check complete, sir. I am listening at your convenience.")
        except Exception as e:
            log.warning(f"voice test failed: {e}", exc_info=True)
            await send_event({"type": "error", "message": f"Voice test failed: {e}"})
        finally:
            await set_state("idle")


async def _check_for_updates():
    await asyncio.sleep(10)
    try:
        from core.updater import check_for_update
        info = await asyncio.to_thread(check_for_update)
        if info:
            log.info("Update available: %s (current: %s)", info["latest"], config.VERSION)
            await send_event({
                "type": "update_available",
                "current": info["current"],
                "latest": info["latest"],
                "url": info["url"],
                "download_url": info.get("download_url", ""),
                "size": info.get("size", 0),
            })
    except Exception:
        pass


@app.on_event("startup")
async def startup():
    global main_loop
    main_loop = asyncio.get_running_loop()
    asyncio.create_task(_check_for_updates())
    mic.on_wake = _on_wake_from_audio_thread
    if config.WAKE_ENABLED_DEFAULT:
        try:
            mic.enable_wake()
            update_wake_arm()
            await send_event({"type": "wake", "enabled": mic.wake_enabled})
        except Exception as e:
            log.warning(f"wake autostart failed: {e}")
    asyncio.create_task(level_broadcaster())
    asyncio.create_task(startup_greeting())
    asyncio.create_task(away_watcher())
    netmsg.start_receiver()

    async def _on_reminder(msg):
        await send_event({"type": "reminder", "message": msg})
        try:
            from core.net import telegram_bot
            telegram_bot.telegram_notify(f"⏰ Reminder: {msg}")
        except Exception:
            pass
        speaker.stop()
        await send_event({"type": "state", "value": "speaking"})
        await speaker.speak(f"Reminder: {msg}")
        await set_state("idle")

    from core import scheduler
    scheduler.init(main_loop, _on_reminder)
    scheduler.start_checker()
    try:
        from core.net import telegram_bot
        telegram_bot.start()
    except Exception as e:
        log.warning(f"telegram start failed: {e}")


_startup_audio = None


def _load_startup_sound():
    global _startup_audio
    if _startup_audio is not None:
        return _startup_audio
    path = config.resolve_path(config.STARTUP_SOUND)
    if path and os.path.exists(path):
        import miniaudio
        decoded = miniaudio.decode(open(path, "rb").read())
        samples = np.array(decoded.samples, dtype=np.int16)
        nch = getattr(decoded, "nchannels", 1)
        if nch > 1:
            samples = samples.reshape(-1, nch)
        _startup_audio = (samples, decoded.sample_rate)
    return _startup_audio


async def startup_greeting():
    await asyncio.sleep(2.5)
    audio = await asyncio.get_running_loop().run_in_executor(None, _load_startup_sound)
    if audio:
        log.info("[greet] startup: playing custom startup sound")
        def play():
            try:
                sd.play(audio[0], audio[1])
                sd.wait()
            except Exception as e:
                log.warning(f"startup sound play failed: {e}")
        async with processing_lock:
            await set_state("speaking")
            await asyncio.get_running_loop().run_in_executor(None, play)
            await set_state("idle")
        update_wake_arm()
        return
    from core.greeter import start_phrase
    phrase = start_phrase()
    log.info(f"[greet] startup: {phrase}")
    async with processing_lock:
        await set_state("speaking")
        try:
            await speaker.speak(phrase)
        except Exception as e:
            log.warning(f"greeting speech failed: {e}")
        await set_state("idle")
    update_wake_arm()


AWAY_THRESHOLD_SECONDS = 15 * 60
away_flag = {"active": False, "since": None}


async def away_watcher():
    from core.greeter import get_idle_seconds, welcome_back_phrase
    while True:
        await asyncio.sleep(10)
        try:
            idle = get_idle_seconds()
        except Exception:
            continue
        if idle > AWAY_THRESHOLD_SECONDS and not away_flag["active"]:
            away_flag["active"] = True
            away_flag["since"] = datetime.now()
            log.info(f"[greet] user went idle ({idle // 60} min)")
        elif (
            away_flag["active"]
            and idle < 5
            and current_state == "idle"
            and not processing_lock.locked()
        ):
            away_flag["active"] = False
            minutes = 15
            if away_flag["since"]:
                minutes = max(1, int((datetime.now() - away_flag["since"]).total_seconds() / 60))
            phrase = welcome_back_phrase(minutes)
            log.info(f"[greet] welcome back after {minutes} min: {phrase}")
            asyncio.create_task(speak_greeting(phrase))


async def speak_greeting(phrase):
    async with processing_lock:
        await set_state("speaking")
        try:
            await speaker.speak(phrase)
        except Exception as e:
            log.warning(f"greeting speech failed: {e}")
        finally:
            await set_state("idle")
    update_wake_arm()
