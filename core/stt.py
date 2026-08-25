import os
import time

import numpy as np
import sounddevice as sd

import core.config as config

SAMPLE_RATE = 16000


class Transcriber:
    def __init__(self):
        self._model = None

    def preload(self):
        return self._load()

    def _load(self):
        if self._model is None:
            from faster_whisper import WhisperModel
            models_dir = config.models_dir()
            local = os.path.join(models_dir, "tiny.en") if models_dir else ""
            if local and os.path.isfile(os.path.join(local, "model.bin")):
                print(f"[stt] using bundled model: {local}")
                self._model = WhisperModel(local, device="cpu", compute_type="int8")
            else:
                print(f"[stt] using downloaded model: {config.STT_MODEL}")
                self._model = WhisperModel(config.STT_MODEL, device="cpu", compute_type="int8")
        return self._model

    def transcribe_array(self, audio_float32):
        duration = len(audio_float32) / SAMPLE_RATE
        if duration < 0.6:
            return ""
        model = self._load()
        segments, info = model.transcribe(audio_float32, language="en", beam_size=1, vad_filter=True)
        return " ".join(s.text.strip() for s in segments).strip()


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
