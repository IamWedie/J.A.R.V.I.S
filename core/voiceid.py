import os
import struct
import threading
import wave

import numpy as np

SAMPLE_RATE = 16000
MODEL_NAME = "redimnet-b2"


def data_dir():
    base = os.environ.get("JARVIS_DATA_DIR")
    if not base:
        if getattr(os, "frozen", False):
            base = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "JARVIS")
        else:
            base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    os.makedirs(base, exist_ok=True)
    return base


def trim_silence(wav, frame_ms=30, top_db=28):
    wav = np.asarray(wav, dtype=np.float32)
    frame = int(SAMPLE_RATE * frame_ms / 1000)
    n = len(wav) // frame
    if n == 0:
        return wav
    frames = wav[: n * frame].reshape(n, frame)
    rms = np.sqrt(np.mean(frames ** 2, axis=1))
    db = 20 * np.log10(rms + 1e-9)
    mask = db > (db.max() - top_db)
    idx = np.where(mask)[0]
    if len(idx) == 0:
        return wav
    pad = 3
    start = max(0, idx[0] - pad) * frame
    end = min(n, idx[-1] + pad) * frame
    return wav[start:end]


def _save_temp_wav(audio_float32):
    path = os.path.join(data_dir(), "_tmp_voice.wav")
    audio = np.clip(np.asarray(audio_float32, dtype=np.float32), -1.0, 1.0)
    pcm = (audio * 32767.0).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(pcm.tobytes())
    return path


class VoiceID:
    def __init__(self):
        self.embedder = None
        self.threshold = 0.55
        self._lock = threading.Lock()
        self.path = os.path.join(data_dir(), "owner_voice.npy")
        self.embedding = None
        self.last_similarity = None
        self.load()

    @property
    def enrolled(self):
        return self.embedding is not None

    def load(self):
        if os.path.exists(self.path):
            try:
                emb = np.load(self.path)
                if emb.ndim == 1 and 64 <= emb.shape[0] <= 1024:
                    self.embedding = emb
                    return True
            except Exception:
                pass
        return False

    def reset(self):
        self.embedding = None
        if os.path.exists(self.path):
            try:
                os.remove(self.path)
            except Exception:
                pass

    def _load_embedder(self):
        if self.embedder is None:
            from speakeronnx import SpeakerEmbedder
            self.embedder = SpeakerEmbedder(model=MODEL_NAME)
        return self.embedder

    def _embed(self, wav):
        enc = self._load_embedder()
        trimmed = trim_silence(np.asarray(wav, dtype=np.float32))
        if len(trimmed) < SAMPLE_RATE:
            trimmed = wav
        wav_path = _save_temp_wav(trimmed)
        with self._lock:
            emb = enc.embed(wav_path)
        try:
            os.remove(wav_path)
        except Exception:
            pass
        emb = np.asarray(emb, dtype=np.float32).flatten()
        return emb / (np.linalg.norm(emb) + 1e-9)

    def enroll(self, audio_samples):
        embeddings = [self._embed(w) for w in audio_samples]
        mean_emb = np.mean(embeddings, axis=0)
        self.embedding = mean_emb / (np.linalg.norm(mean_emb) + 1e-9)
        try:
            np.save(self.path, self.embedding)
        except Exception as e:
            print(f"failed saving voice print: {e}")
        return True

    def verify(self, wav):
        if not self.enrolled:
            return True
        emb = self._embed(wav)
        sim = float(np.dot(emb, self.embedding))
        with self._lock:
            self.last_similarity = round(sim, 3)
        print(f"voice similarity: {sim:.3f} (threshold {self.threshold})")
        return sim >= self.threshold


voiceid = VoiceID()
