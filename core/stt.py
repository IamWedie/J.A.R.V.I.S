import os
import time

import numpy as np
import sounddevice as sd

import core.config as config

SAMPLE_RATE = 16000
ALLOWED_LANGS = {"en", "ar"}

class Transcriber:
    def __init__(self):
        self._model = None
        self._model_name = None

    def preload(self):
        return self._load()

    def _load(self):
        model_name = getattr(config, "STT_MODEL", "tiny.en")
        if self._model is not None and self._model_name == model_name:
            return self._model
        from faster_whisper import WhisperModel
        models_dir = config.models_dir()
        local = os.path.join(models_dir, model_name) if models_dir else ""
        if local and os.path.isfile(os.path.join(local, "model.bin")):
            print(f"[stt] using bundled model: {local}")
            self._model = WhisperModel(local, device="cpu", compute_type="int8")
        else:
            print(f"[stt] using downloaded model: {model_name}")
            self._model = WhisperModel(model_name, device="cpu", compute_type="int8")
        self._model_name = model_name
        return self._model

    def transcribe_array(self, audio_float32):
        duration = len(audio_float32) / SAMPLE_RATE
        if duration < 0.6:
            return ""
        model = self._load()
        lang = getattr(config, "STT_LANG", None)
        if lang == "auto" or (lang is None and getattr(config, "MULTILINGUAL", False)):
            lang = None
        elif lang is None and "tiny.en" in getattr(config, "STT_MODEL", ""):
            lang = "en"
        segments, info = model.transcribe(
            audio_float32,
            language=lang,
            beam_size=1,
            vad_filter=True,
            without_timestamps=True,
            condition_on_previous_text=False,
        )
        text = " ".join(s.text.strip() for s in segments).strip()
        detected = getattr(info, "language", None)
        if lang is None and detected and detected not in ALLOWED_LANGS:
            retry_lang = "ar"
            segments2, _ = model.transcribe(
                audio_float32,
                language=retry_lang,
                beam_size=1,
                vad_filter=True,
                without_timestamps=True,
                condition_on_previous_text=False,
            )
            text2 = " ".join(s.text.strip() for s in segments2).strip()
            if len(text2) >= len(text):
                text = text2
            print(f"[stt] auto-detected '{detected}' (not en/ar), used '{retry_lang}' instead")
        return text


transcriber = Transcriber()


class Listener:
    """Legacy push-to-talk recorder kept for compatibility."""

    def __init__(self):
        self._stream = None
        self._frames = []
        self.recording = False
        self.level = 0.0

    def _audio_callback(self, indata, frames, time_info, status):
        if self.recording:
            self._frames.append(indata.copy())
            rms = float(np.sqrt(np.mean(indata ** 2)))
            self.level = rms

    def start(self):
        if self.recording:
            return False
        self._frames = []
        self.level = 0.0
        self.recording = True
        self._stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="float32",
            blocksize=800,
            callback=self._audio_callback,
        )
        self._stream.start()
        return True

    def stop(self):
        self.recording = False
        self.level = 0.0
        if self._stream:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None

    def transcribe(self):
        if not self._frames:
            return ""
        audio = np.concatenate(self._frames)[:, 0]
        self._frames = []
        from core.stt import transcriber
        return transcriber.transcribe_array(np.concatenate([audio]))


listener = Listener()
