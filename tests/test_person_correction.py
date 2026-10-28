from tools.collection_tools import Call
from tools.rules import now_ist
from tools.store import MemoryStore


def make_call():
    store = MemoryStore.from_files(clock=now_ist)
    return Call(store, "C002", session_id="t1", clock=now_ist)


def test_third_party_can_be_corrected_once():
    c = make_call()
    c.report_person("third_party")
    assert c.report_person("customer")["ok"] is True


def test_only_one_correction_is_allowed():
    c = make_call()
    c.report_person("third_party")
    c.report_person("customer")
    c.report_person("third_party")
    assert c.report_person("customer")["reason"] == "already_third_party"


def test_correction_requires_date_of_birth():
    c = make_call()
    c.report_person("third_party")
    assert c.report_person("customer")["next"] == "ask_date_of_birth"
    assert c.get_due_details() == {"error": "not_unlocked"}