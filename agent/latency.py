"""Per-turn latency logging. Reads the timings LiveKit stores on each chat message."""
import logging

logger = logging.getLogger("tam-voice-agent.latency")


def _ms(seconds):
    return "-" if seconds is None else f"{seconds * 1000:.0f}"


class LatencyLog:
    """One per call. Logs one LATENCY line per agent reply and a summary at the end.

    wait = caller stopped speaking -> agent audio started (what the caller feels).
    It includes end-of-turn detection, STT, every LLM trip, tool calls and TTS.
    """

    def __init__(self):
        self.user_stopped_at = None
        self.user_metrics = {}
        self.tools = 0
        self.turn = 0
        self.waits = []

    def attach(self, session):
        session.on("conversation_item_added", self._on_item)
        session.on("function_tools_executed", self._on_tools)

    def _on_tools(self, ev):
        self.tools += len(ev.function_calls)

    def _on_item(self, ev):
        item = ev.item
        if getattr(item, "type", None) != "message":
            return
        m = item.metrics or {}
        if item.role == "user":
            self.user_stopped_at = m.get("stopped_speaking_at")
            self.user_metrics = m
            self.tools = 0
            return
        if item.role != "assistant":
            return

        self.turn += 1
        wait = None
        started = m.get("started_speaking_at")
        if started and self.user_stopped_at:
            wait = started - self.user_stopped_at
            self.waits.append(wait)
        logger.info(
            "LATENCY turn=%d wait_ms=%s end_of_turn_ms=%s stt_ms=%s llm_ttft_ms=%s "
            "tts_ttfb_ms=%s tools=%d",
            self.turn, _ms(wait), _ms(self.user_metrics.get("end_of_turn_delay")),
            _ms(self.user_metrics.get("transcription_delay")), _ms(m.get("llm_node_ttft")),
            _ms(m.get("tts_node_ttfb")), self.tools)
        # Next reply without new caller speech (e.g. the greeting) has no wait to measure
        self.user_stopped_at, self.user_metrics, self.tools = None, {}, 0

    def summary(self):
        if not self.waits:
            logger.info("LATENCY summary: no measured replies")
            return
        w = sorted(self.waits)
        logger.info("LATENCY summary: replies=%d avg_wait_ms=%s median_ms=%s max_ms=%s",
                    len(w), _ms(sum(w) / len(w)), _ms(w[len(w) // 2]), _ms(w[-1]))
