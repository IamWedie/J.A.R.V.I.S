import threading
import time

import numpy as np
import sounddevice as sd

SAMPLE_RATE = 16000
OWW_CHUNK_BYTES = 12800


class MicService:
    def __init__(self):
        self.stream = None
        self.mode = "idle"
        self.command_frames = []
        self.level = 0.0
        self.stop_requested = False
        self.wake_enabled = False
        self.wake_armed = False
        self.wake_score = 0.0
        self.on_wake = None
        self._wake_model = None
        self._oww_lock = threading.Lock()
        self._buf = bytearray()
        self._last_fire = 0.0

    def request_stop(self):
        if self.mode == "command":
            self.stop_requested = True

    def _load_wake(self):
        if self._wake_model is None:
            from openwakeword.model import Model
            self._wake_model = Model(wakeword_models=["hey_jarvis"], inference_framework="onnx")
        return self._wake_model

    def enable_wake(self):
        if not self.wake_enabled:
            self._load_wake()
            self.start_stream()
            self.wake_enabled = True

    def disable_wake(self):
        self.wake_enabled = False
        self.wake_armed = False

    def start_stream(self):
        if self.stream is None:
            self.stream = sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=1,
                dtype="int16",
                blocksize=1280,
                callback=self._callback,
            )
            self.stream.start()

    def stop_stream(self):
        if self.stream:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception:
                pass
            self.stream = None

    def set_armed(self, armed):
        self.wake_armed = bool(armed and self.wake_enabled)
        if not self.wake_armed:
            self._buf.clear()

    def _callback(self, indata, frames, time_info, status):
        chunk = indata[:, 0]
        self.level = float(np.sqrt(np.mean((chunk.astype(np.float32) / 32768.0) ** 2)))

        if self.mode == "command":
            self.command_frames.append(chunk.copy())

        if not (self.wake_enabled and self.wake_armed and self.on_wake):
            return

        now = time.time()
        if now - self._last_fire < 3.5:
            return

        self._buf.extend(chunk.tobytes())
        while len(self._buf) >= OWW_CHUNK_BYTES:
            piece = np.frombuffer(bytes(self._buf[:OWW_CHUNK_BYTES]), dtype=np.int16)
            del self._buf[:OWW_CHUNK_BYTES]
            try:
                with self._oww_lock:
                    pred = self._wake_model.predict(piece)
                    score = max(pred.values()) if isinstance(pred, dict) else float(pred)
                self.wake_score = score
                if score > 0.5:
                    self._last_fire = now
                    with self._oww_lock:
                        self._wake_model.reset()
                    cb = self.on_wake
                    if cb:
                        cb()
                        return
            except Exception:
                pass

    def begin_command_capture(self):
        self.command_frames = []
        self.level = 0.0
        self.stop_requested = False
        self.mode = "command"

    def end_command_capture(self):
        frames = self.command_frames
        self.command_frames = []
        self.mode = "idle"
        if not frames:
            return None
        audio = np.concatenate(frames).astype(np.float32) / 32768.0
        return audio


mic = MicService()
