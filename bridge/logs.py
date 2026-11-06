"""Logging for the bridge: our INFO lines, LiveKit's warnings and errors, nothing else."""
import logging
from pathlib import Path


class DropLiveKitInfo(logging.Filter):
    """livekit.rtc.room logs "ignoring byte stream with topic 'lk.agent.session'" and
    "ignoring text stream with topic 'lk.transcription'" with logging.info(), i.e. on the
    root logger, not on its "livekit" logger, so setting "livekit" to WARNING misses them.
    On the root logger, drop records below WARNING that come from the livekit package."""

    def filter(self, record):
        return not (record.levelno < logging.WARNING
                    and "livekit" in Path(record.pathname).parts)


def setup():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("livekit").setLevel(logging.WARNING)
    # A filter on the root logger only sees records logged on the root logger itself
    # (logging.info(...)), so the "bridge" logger's records are untouched.
    root = logging.getLogger()
    if not any(isinstance(f, DropLiveKitInfo) for f in root.filters):
        root.addFilter(DropLiveKitInfo())
