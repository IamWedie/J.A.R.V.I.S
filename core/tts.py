import asyncio
import os
import tempfile

import edge_tts
import numpy as np
import sounddevice as sd

import core.config as config


class Speaker:
    def __init__(self):
        self.speaking = False
        self.rate = None

    async def speak(self, text, voice=None):
        voice = voice or config.TTS_VOICE
        rate = self.rate if self.rate is not None else config.TTS_RATE
        text = text.strip()
        if not text:
            return
        tmp_path = None
        try:
            fd, tmp_path = tempfile.mkstemp(suffix=".mp3")
            os.close(fd)
            communicate = edge_tts.Communicate(text, voice, rate=rate)
            await communicate.save(tmp_path)

            import miniaudio
            loop = asyncio.get_running_loop()

            def prepare():
                with open(tmp_path, "rb") as f:
                    data = f.read()
                decoded = miniaudio.decode(data)
                samples = np.array(decoded.samples, dtype=np.int16)
                if getattr(decoded, "nchannels", 1) > 1:
                    samples = samples.reshape(-1, decoded.nchannels)
                dur = len(samples) / decoded.sample_rate
                print(f"[tts] voice={voice} rate={rate} sr={decoded.sample_rate} "
                      f"channels={getattr(decoded, 'nchannels', '?')} chars={len(text)} audio_dur={dur:.2f}s")
                return samples, decoded.sample_rate

            samples, rate = await loop.run_in_executor(None, prepare)

            def play_blocking():
                self.speaking = True
                try:
                    sd.play(samples, rate)
                    sd.wait()
                except Exception:
                    pass
                finally:
                    self.speaking = False

            await loop.run_in_executor(None, play_blocking)
        finally:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass

    def stop(self):
        try:
            sd.stop()
        except Exception:
            pass
        self.speaking = False


speaker = Speaker()
