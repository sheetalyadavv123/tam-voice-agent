import re
from pathlib import Path

from tools.collection_tools import OUTCOMES
from tools.scenario_runner import TOOLS

PROMPT = (Path(__file__).parent.parent / "prompts" / "collections.md").read_text(encoding="utf-8")


def test_prompt_mentions_every_tool():
    # verify_identity is not the AI's: the code reads the date of birth itself
    missing = [t for t in TOOLS if t not in PROMPT and t != "verify_identity"]
    assert missing == [], f"prompt never mentions: {missing}"


def test_prompt_lists_every_outcome_label():
    missing = [o for o in OUTCOMES if o not in PROMPT]
    assert missing == [], f"prompt never mentions outcomes: {missing}"


def test_prompt_only_placeholder_is_company():
    assert set(re.findall(r"\{(\w+)\}", PROMPT)) == {"company"}


def test_prompt_stays_short():
    assert len(PROMPT.split()) < 1100