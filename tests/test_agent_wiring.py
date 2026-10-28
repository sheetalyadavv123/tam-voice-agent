import ast
import inspect
import subprocess
import sys
from pathlib import Path
from typing import get_args

from tools.collection_tools import (
    CALLBACK_REASONS, OUTCOMES, PERSON_STATUSES, AiCallbackReason, Call,
    Channel, Outcome, PersonStatus,
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


def test_ai_callback_reasons_are_allowed_ones():
    assert set(get_args(AiCallbackReason)) <= CALLBACK_REASONS


def test_agent_registers_exactly_the_nine_tools():
    assert set(_agent_tools()) == set(TOOLS)


def test_agent_tool_inputs_match_the_call_methods():
    for name, params in _agent_tools().items():
        real = [p for p in inspect.signature(getattr(Call, name)).parameters if p != "self"]
        assert params == real, f"{name}: agent takes {params}, Call takes {real}"


def test_agent_module_imports_cleanly():
    run = subprocess.run(
        [sys.executable, "-c", "import sys; sys.path.insert(0, 'agent'); import main"],
        cwd=ROOT, capture_output=True, text=True)
    assert run.returncode == 0, run.stderr[-800:]