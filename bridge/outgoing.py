"""The agent's speech going to the dialer, following SAMPARK's mark rule:
audio streams out as it comes, a mark follows each finished utterance, and the
next utterance waits until the dialer sends that mark back."""
import asyncio

from bridge.audio import CHUNK_BYTES, ulaw_to_b64


class _Utterance:
    def __init__(self):
        self.buf = bytearray()
        self.ended = False


class Outgoing:
    def __init__(self, send, call_number):
        self.send = send  # async function taking a dict
        self.call_number = call_number
        self.utts = []
        self.waiting = False  # a mark is out and not back yet
        self.seq = 0
        self.marks = 0
        self.lock = asyncio.Lock()

    async def audio(self, ulaw: bytes):
        if not self.utts or self.utts[-1].ended:
            self.utts.append(_Utterance())
        self.utts[-1].buf += ulaw
        await self._pump()

    async def end(self):
        if self.utts and not self.utts[-1].ended:
            self.utts[-1].ended = True
            await self._pump()

    async def mark_back(self):
        self.waiting = False
        await self._pump()

    async def clear(self):
        async with self.lock:
            self.utts.clear()
            self.waiting = False
            await self.send({"event": "clear"})

    def idle(self):
        return not self.waiting and not self.utts

    async def _pump(self):
        async with self.lock:
            while self.utts and not self.waiting:
                u = self.utts[0]
                full = len(u.buf) if u.ended else len(u.buf) - len(u.buf) % CHUNK_BYTES
                for i in range(0, full, CHUNK_BYTES):
                    await self._media(bytes(u.buf[i:i + CHUNK_BYTES]))
                del u.buf[:full]
                if not u.ended:
                    return  # still being spoken; more audio will come
                self.marks += 1
                await self.send({"event": "mark", "call_number": self.call_number,
                                 "mark": {"name": f"utt_{self.marks}"}})
                self.waiting = True
                self.utts.pop(0)

    async def _media(self, chunk: bytes):
        self.seq += 1
        await self.send({"event": "media", "call_number": self.call_number,
                         "media": {"chunk_seq_no": self.seq, "timestamp": self.seq * 20,
                                   "payload": ulaw_to_b64(chunk)}})