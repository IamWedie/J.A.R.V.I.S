import asyncio
import os
import re
import tempfile

import edge_tts
import numpy as np
import sounddevice as sd

import core.config as config

SENTENCE_END = re.compile(r"[.!?…](\s|$)")


class Speaker:
    def __init__(self):
        self.speaking = False
        self.rate = None
        self._cancelled = False

    def _decode_mp3(self, path):
        import miniaudio
        with open(path, "rb") as f:
            data = f.read()
        decoded = miniaudio.decode(data)
        samples = np.array(decoded.samples, dtype=np.int16)
        nch = getattr(decoded, "nchannels", 1)
        if nch > 1:
            samples = samples.reshape(-1, nch)
        dur = len(samples) / decoded.sample_rate
        print(f"[tts] voice={config.TTS_VOICE} rate={self.rate or config.TTS_RATE} "
              f"sr={decoded.sample_rate} audio_dur={dur:.2f}s")
        return samples, decoded.sample_rate

    async def _synthesize(self, sentence, out_q):
        tmp_path = None
        try:
            fd, tmp_path = tempfile.mkstemp(suffix=".mp3")
            os.close(fd)
            voice = config.TTS_VOICE
            rate = self.rate if self.rate is not None else config.TTS_RATE
            await edge_tts.Communicate(sentence, voice, rate=rate).save(tmp_path)
            loop = asyncio.get_running_loop()
            item = await loop.run_in_executor(None, self._decode_mp3, tmp_path)
            if not self._cancelled:
                await out_q.put(item)
        except Exception as e:
            print(f"tts synth failed: {e}")
        finally:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass

    async def speak_from_queue(self, text_q):
        synth_q = asyncio.Queue()
        self._cancelled = False

        async def producer():
            buf = ""
            done = False
            while not done and not self._cancelled:
                chunk = await text_q.get()
                if chunk is None:
                    done = True
                else:
                    buf += chunk
                while True:
                    m = SENTENCE_END.search(buf)
                    if not m:
                        break
                    if m.end() < 15 and not done:
                        break
                    sentence = buf[: m.end()].strip()
                    buf = buf[m.end():]
                    if sentence:
                        await self._synthesize(sentence, synth_q)
            if buf.strip() and not self._cancelled:
                await self._synthesize(buf.strip(), synth_q)
            await synth_q.put(None)

        async def consumer():
            loop = asyncio.get_running_loop()
            while True:
                item = await synth_q.get()
                if item is None:
                    break
                if self._cancelled:
                    try:
                        sd.stop()
                    except Exception:
                        pass
                    break
                samples, sr = item
                self.speaking = True

                def play():
                    try:
                        sd.play(samples, sr)
                        sd.wait()
                    except Exception:
                        pass

                await loop.run_in_executor(None, play)
                self.speaking = False

        self.speaking = True
        try:
            await asyncio.gather(producer(), consumer())
        finally:
            self.speaking = False

    async def speak(self, text, voice=None):
        text = text.strip()
        if not text:
            return
        q = asyncio.Queue()
        await q.put(text)
        await q.put(None)
        await self.speak_from_queue(q)

    def stop(self):
        self._cancelled = True
        try:
            sd.stop()
        except Exception:
            pass
        self.speaking = False


speaker = Speaker()
