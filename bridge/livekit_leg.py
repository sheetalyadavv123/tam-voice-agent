"""One call's link to LiveKit. The room already exists (created at /sampark, see
bridge/rooms.py); the dialer's caller joins it as a participant, the agent joins the
same room, and audio flows both ways at 8 kHz."""
import asyncio
import json
import logging
import os
import time

import numpy as np
from livekit import api, rtc

from bridge.audio import SAMPLE_RATE, pcm16_to_ulaw, ulaw_to_pcm16
from bridge.rooms import room_name, rooms
from bridge.timeline import Timeline

log = logging.getLogger("bridge")
SILENCE_RMS = 200          # a frame quieter than this counts as silence
END_OF_UTTERANCE_MS = 500  # this much silence after speech ends a sentence (then a mark)


class LiveKitLeg:
    def __init__(self, call_number, customer_id, on_audio, on_utterance_end, on_clear, on_agent_left,
                 timeline=None):
        self.call_number = call_number
        self.customer_id = customer_id
        self.on_audio = on_audio
        self.on_utterance_end = on_utterance_end
        self.on_clear = on_clear
        self.on_agent_left = on_agent_left
        self.url = os.environ["LIVEKIT_URL"]
        self.key = os.environ["LIVEKIT_API_KEY"]
        self.secret = os.environ["LIVEKIT_API_SECRET"]
        self.room_name = room_name(call_number)
        self.identity = f"caller-{call_number}"
        self.room = None
        self.source = None
        self.tasks = []
        self.speaking = False
        self.last_frame = 0.0
        self.closing = False
        self.timeline = timeline or Timeline(call_number)

    async def start(self):
        token = (api.AccessToken(self.key, self.secret)
                 .with_identity(self.identity).with_name("caller")
                 .with_grants(api.VideoGrants(room_join=True, room=self.room_name))
                 .to_jwt())
        self.room = rtc.Room()
        self.room.on("track_subscribed", self._on_track)
        self.room.on("data_received", self._on_data)
        self.room.on("participant_connected", self._on_joined)
        self.room.on("participant_disconnected", self._on_left)
        self.room.on("disconnected", self._on_disconnected)
        await self.room.connect(self.url, token)
        self.timeline.mark("caller joined")
        for p in list(self.room.remote_participants.values()):  # the agent got in first
            self._on_joined(p)
        self.source = rtc.AudioSource(SAMPLE_RATE, 1)
        track = rtc.LocalAudioTrack.create_audio_track("caller", self.source)
        await self.room.local_participant.publish_track(
            track, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE))
        self.tasks.append(asyncio.create_task(self._watchdog()))
        log.info("room %s ready for customer %s", self.room_name, self.customer_id)

    async def caller_audio(self, ulaw: bytes):
        """20 ms of the customer's voice from the dialer, into the room."""
        if self.source is None:
            return
        pcm = ulaw_to_pcm16(ulaw)
        await self.source.capture_frame(rtc.AudioFrame(pcm.tobytes(), SAMPLE_RATE, 1, len(pcm)))

    def _on_joined(self, participant):
        log.info("joined room: %s", participant.identity)
        if participant.identity != self.identity:
            self.timeline.mark("agent joined")

    def _on_track(self, track, publication, participant):
        if track.kind == rtc.TrackKind.KIND_AUDIO and participant.identity != self.identity:
            log.info("agent audio from %s", participant.identity)
            self.timeline.mark("agent audio track")
            self.tasks.append(asyncio.create_task(self._read_agent(track)))

    async def _read_agent(self, track):
        stream = rtc.AudioStream(track, sample_rate=SAMPLE_RATE, num_channels=1)
        quiet = bytearray()
        quiet_ms = 0.0
        async for ev in stream:
            pcm = np.frombuffer(ev.frame.data, dtype=np.int16)
            if pcm.size == 0:
                continue
            self.last_frame = time.monotonic()
            ulaw = pcm16_to_ulaw(pcm)
            loud = np.sqrt(np.mean(pcm.astype(np.float32) ** 2)) >= SILENCE_RMS
            if loud:
                self.timeline.mark("first agent audio")
                if quiet:  # a short pause inside a sentence: keep it
                    await self.on_audio(bytes(quiet))
                    quiet.clear()
                self.speaking = True
                quiet_ms = 0.0
                await self.on_audio(ulaw)
            elif self.speaking:
                quiet += ulaw
                quiet_ms += pcm.size * 1000 / SAMPLE_RATE
                if quiet_ms >= END_OF_UTTERANCE_MS:
                    self.speaking = False
                    quiet.clear()
                    quiet_ms = 0.0
                    await self.on_utterance_end()

    async def _watchdog(self):
        # If the agent's track stops sending frames mid-sentence, still end the sentence
        while True:
            await asyncio.sleep(0.1)
            if self.speaking and time.monotonic() - self.last_frame > END_OF_UTTERANCE_MS / 1000:
                self.speaking = False
                await self.on_utterance_end()

    def _on_data(self, packet):
        if packet.topic != "sampark":
            return
        msg = json.loads(packet.data)
        if msg.get("type") == "clear":
            log.info("agent interrupted: clear")
            self.speaking = False
            asyncio.create_task(self.on_clear())

    def _on_left(self, participant):
        if participant.identity != self.identity:
            log.info("left room: %s", participant.identity)
            asyncio.create_task(self.on_agent_left())

    def _on_disconnected(self, reason=None):
        # The agent ending the call deletes the room; treat that like the agent leaving
        if not self.closing:
            log.info("room closed (%s)", reason)
            asyncio.create_task(self.on_agent_left())

    async def close(self):
        self.closing = True
        for t in self.tasks:
            t.cancel()
        try:
            if self.room:
                await self.room.disconnect()
        except Exception:
            log.exception("disconnect failed for %s", self.room_name)
        await rooms.delete(self.room_name)