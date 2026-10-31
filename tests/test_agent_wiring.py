import ast
import inspect
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import get_args

import pytest

from tools.collection_tools import (
    CALLBACK_REASONS, HANDOFF_AFTER_PROMISE, HANDOFF_REASONS, OUTCOMES, PERSON_STATUSES,
    AiCallbackReason, Call, Channel, HandoffReason, Outcome, PersonStatus,
)
from tools.scenario_runner import TOOLS
from tools.store import CHANNELS

ROOT = Path(__file__).parent.parent
MAIN_SRC = (ROOT / "agent" / "main.py").read_text(encoding="utf-8")


def _agent_tools():
    tree = ast.parse(MAIN_SRC)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Collector")
    found = {}
    for fn in cls.body:
        if isinstance(fn, ast.AsyncFunctionDef) and any(
                "function_tool" in ast.unparse(d) for d in fn.decorator_list):
            found[fn.name] = [a.arg for a in fn.args.args if a.arg not in ("self", "context")]
    return found


def test_fixed_choices_match_the_rules():
    assert set(get_args(Outcome)) == OUTCOMES
    assert set(get_args(PersonStatus)) == PERSON_STATUSES
    assert set(get_args(Channel)) == CHANNELS
    assert set(get_args(HandoffReason)) == HANDOFF_REASONS
    assert HANDOFF_AFTER_PROMISE <= HANDOFF_REASONS


def test_ai_callback_reasons_are_allowed_ones():
    assert set(get_args(AiCallbackReason)) <= CALLBACK_REASONS


def test_agent_registers_the_tools_except_the_dob_check():
    # the code reads the date of birth itself, so the AI has no verify_identity tool
    assert set(_agent_tools()) == set(TOOLS) - {"verify_identity"}


def test_agent_tool_inputs_match_the_call_methods():
    for name, params in _agent_tools().items():
        real = [p for p in inspect.signature(getattr(Call, name)).parameters if p != "self"]
        assert params == real, f"{name}: agent takes {params}, Call takes {real}"


def test_agent_module_imports_cleanly():
    run = subprocess.run(
        [sys.executable, "-c", "import sys; sys.path.insert(0, 'agent'); import main"],
        cwd=ROOT, capture_output=True, text=True)
    assert run.returncode == 0, run.stderr[-800:]

def test_session_marks_the_caller_away_after_8_seconds():
    assert "user_away_timeout=USER_AWAY_SECONDS" in MAIN_SRC
    assert re.search(r"^USER_AWAY_SECONDS = 8$", MAIN_SRC, re.M)


def _pace(value):
    env = {**os.environ, "TTS_PACE": value}
    return subprocess.run(
        [sys.executable, "-c", "import sys; sys.path.insert(0, 'agent'); import config; print(config.TTS_PACE)"],
        cwd=ROOT, capture_output=True, text=True, env=env)


@pytest.mark.parametrize("value, expected", [("", "0.9"), ("0.5", "0.5"), ("2.0", "2.0"), ("1.1", "1.1")])
def test_tts_pace_from_env(value, expected):
    run = _pace(value)
    assert run.returncode == 0 and run.stdout.strip() == expected


@pytest.mark.parametrize("value", ["0.4", "2.1", "-1"])
def test_tts_pace_out_of_range_stops_startup(value):
    run = _pace(value)
    assert run.returncode != 0 and "TTS_PACE must be between 0.5 and 2.0" in run.stderr



def test_startup_logs_stt_language_and_model():
    assert 'logger.info("STT_LANGUAGE=%s LLM_MODEL=%s", config.STT_LANGUAGE, config.LLM_MODEL)' in MAIN_SRC


def test_third_party_disclosure_defaults_to_dob_verified():
    from tools import policy
    assert policy.THIRD_PARTY_DISCLOSURE == (os.getenv("THIRD_PARTY_DISCLOSURE") or "dob_verified")
