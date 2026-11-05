import asyncio

from bridge.outgoing import Outgoing


def events_for(script):
    sent = []

    async def send(msg):
        sent.append(msg)

    async def go():
        out = Outgoing(send, "c1")
        await script(out, sent)

    asyncio.run(go())
    return [m["event"] for m in sent]


def test_next_sentence_waits_for_the_mark():
    async def script(out, sent):
        await out.audio(b"\x01" * 400)   # 2 full chunks + 80 bytes
        await out.end()                  # rest + mark
        await out.audio(b"\x02" * 320)   # second sentence: must wait
        await out.end()
        assert [m["event"] for m in sent] == ["media", "media", "media", "mark"]
        await out.mark_back()            # now it goes
    assert events_for(script) == ["media", "media", "media", "mark", "media", "media", "mark"]


def test_clear_drops_waiting_audio():
    async def script(out, sent):
        await out.audio(b"\x01" * 160)
        await out.end()
        await out.audio(b"\x02" * 320)   # waiting for the mark
        await out.clear()
        assert out.idle()
    assert events_for(script) == ["media", "mark", "clear"]