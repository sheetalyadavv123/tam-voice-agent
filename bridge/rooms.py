"""LiveKit rooms for SAMPARK calls, through one LiveKitAPI client for the whole process
(a new client per call costs a new HTTPS connection every time)."""
import asyncio
import json
import logging
import os

from livekit import api

log = logging.getLogger("bridge")


def room_name(call_number):
    return f"sampark-{call_number}"


def http_url(url: str) -> str:
    return url.replace("wss://", "https://", 1).replace("ws://", "http://", 1)


def room_gone(e: api.TwirpError) -> bool:
    """The room was already deleted (the agent ends a call by deleting it). LiveKit
    answers 404; the body is not always Twirp JSON, so the code can be "unknown"."""
    return e.status == 404 or e.code == api.TwirpErrorCode.NOT_FOUND


class Rooms:
    def __init__(self):
        self._lk = None
        self._loop = None

    def _client(self):
        loop = asyncio.get_running_loop()
        if self._lk is None or self._loop is not loop:  # its HTTP session belongs to one loop
            self._lk = api.LiveKitAPI(http_url(os.environ["LIVEKIT_URL"]),
                                      os.environ["LIVEKIT_API_KEY"], os.environ["LIVEKIT_API_SECRET"])
            self._loop = loop
        return self._lk

    async def create(self, name, metadata: dict):
        """Creating the room is what dispatches the agent (automatic dispatch)."""
        await self._client().room.create_room(api.CreateRoomRequest(
            name=name, metadata=json.dumps(metadata), empty_timeout=60))

    async def delete(self, name):
        try:
            await self._client().room.delete_room(api.DeleteRoomRequest(room=name))
        except api.TwirpError as e:
            if room_gone(e):
                log.info("room %s already deleted", name)
            else:
                log.warning("could not delete room %s: %s", name, e)

    async def aclose(self):
        if self._lk is not None:
            await self._lk.aclose()
            self._lk = None


rooms = Rooms()
