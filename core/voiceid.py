import os
import threading

import numpy as np

SAMPLE_RATE = 16000


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


class VoiceID:
    def __init__(self):
        self.encoder = None
        self.embedding = None
        self.threshold = 0.72
        self._lock = threading.Lock()
        self.path = os.path.join(data_dir(), "owner_voice.npy")
        self.last_similarity = None
        self.load()

    @property
    def enrolled(self):
        return self.embedding is not None

    def load(self):
        if os.path.exists(self.path):
            try:
                emb = np.load(self.path)
                if emb.ndim == 1 and emb.shape[0] == 256:
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

    def _load_encoder(self):
        if self.encoder is None:
            from resemblyzer import VoiceEncoder
            self.encoder = VoiceEncoder("cpu", verbose=False)
        return self.encoder

    def _embed(self, wav):
        enc = self._load_encoder()
        trimmed = trim_silence(np.asarray(wav, dtype=np.float32))
        if len(trimmed) < SAMPLE_RATE * 0.8:
            trimmed = wav
        emb = enc.embed_utterance(trimmed.astype(np.float32))
        return emb / (np.linalg.norm(emb) + 1e-9)

    def enroll(self, audio_samples):
        embeddings = []
        for wav in audio_samples:
            embeddings.append(self._embed(wav))
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
