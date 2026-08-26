import os
import struct
import threading
import wave

import numpy as np

from core.logging_setup import get_logger

log = get_logger("voiceid")

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
        self.legacy_path = os.path.join(data_dir(), "owner_voice.npy")
        self.profiles_dir = os.path.join(data_dir(), "voices")
        os.makedirs(self.profiles_dir, exist_ok=True)
        self.profiles = {}
        self.last_similarity = None
        self.last_identity = None
        self._migrate_legacy()
        self.load_all()

    def _migrate_legacy(self):
        if os.path.exists(self.legacy_path):
            try:
                emb = np.load(self.legacy_path)
                if emb.ndim == 1 and 64 <= emb.shape[0] <= 1024:
                    np.save(os.path.join(self.profiles_dir, "sir.npy"), emb)
                os.remove(self.legacy_path)
            except Exception:
                pass

    @property
    def enrolled(self):
        return len(self.profiles) > 0

    @property
    def names(self):
        return sorted(self.profiles.keys())

    def _profile_path(self, name):
        safe = "".join(c for c in str(name).strip().lower() if c.isalnum() or c in "_- ")[:40].strip().replace(" ", "_")
        return os.path.join(self.profiles_dir, f"{safe or 'user'}.npy")

    def load_all(self):
        self.profiles = {}
        try:
            for fn in os.listdir(self.profiles_dir):
                if not fn.endswith(".npy"):
                    continue
                try:
                    emb = np.load(os.path.join(self.profiles_dir, fn))
                    if emb.ndim == 1 and 64 <= emb.shape[0] <= 1024:
                        self.profiles[fn[:-4]] = emb
                except Exception:
                    continue
        except Exception:
            pass
        return self.profiles

    def reset(self, name=None):
        with self._lock:
            if name is None:
                self.profiles.clear()
                try:
                    for fn in os.listdir(self.profiles_dir):
                        if fn.endswith(".npy"):
                            os.remove(os.path.join(self.profiles_dir, fn))
                except Exception:
                    pass
            else:
                path = self._profile_path(name)
                self.profiles.pop(os.path.splitext(os.path.basename(path))[0], None)
                if os.path.exists(path):
                    try:
                        os.remove(path)
                    except Exception:
                        pass

    def _load_embedder(self):
        if self.embedder is None:
            from speakeronnx import SpeakerEmbedder

            import core.config as config
            models_dir = config.models_dir()
            local = os.path.join(models_dir, "redimnet_b2_vox2.onnx") if models_dir else ""
            if local and os.path.isfile(local):
                log.info("using bundled model: %s", local)
                self.embedder = SpeakerEmbedder(model=local)
            else:
                log.info("using downloaded model: %s", MODEL_NAME)
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

    def enroll(self, audio_samples, name="sir"):
        embeddings = [self._embed(w) for w in audio_samples]
        mean_emb = np.mean(embeddings, axis=0)
        mean_emb = mean_emb / (np.linalg.norm(mean_emb) + 1e-9)
        path = self._profile_path(name)
        try:
            np.save(path, mean_emb)
        except Exception as e:
            log.error("failed saving voice print: %s", e)
            return False
        with self._lock:
            self.profiles[os.path.splitext(os.path.basename(path))[0]] = mean_emb
            self.last_identity = str(name).strip().lower() or "sir"
        return True

    def verify(self, wav):
        if not self.enrolled:
            return True
        emb = self._embed(wav)
        best = None
        for name, ref in self.profiles.items():
            sim = float(np.dot(emb, ref))
            if best is None or sim > best[1]:
                best = (name, sim)
        name, sim = best
        with self._lock:
            self.last_similarity = round(sim, 3)
            self.last_identity = name if sim >= self.threshold else self.last_identity
        log.debug("voice similarity: %.3f vs '%s' (threshold %s)", sim, name, self.threshold)
        return sim >= self.threshold

    def identify(self, wav):
        if not self.enrolled:
            return None
        emb = self._embed(wav)
        best_name, best_sim = None, -1.0
        for name, ref in self.profiles.items():
            sim = float(np.dot(emb, ref))
            if sim > best_sim:
                best_name, best_sim = name, sim
        with self._lock:
            self.last_similarity = round(best_sim, 3)
            if best_sim >= self.threshold:
                self.last_identity = best_name
                return best_name
        return None


voiceid = VoiceID()
