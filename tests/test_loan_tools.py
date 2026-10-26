from tools.loan_tools import (
    Session, _customers, find_customer, verify_identity,
    get_loan_status, record_outcome, transfer_to_human,
)


def verified_session(phone, dob):
    """Helper: start a call, find the customer, and verify them."""
    s = Session()
    find_customer(s, phone)
    verify_identity(s, dob)
    return s


def test_find_customer_normalizes_phone():
    assert find_customer(Session(), "+91 90000 00001")["found"] is True


def test_unknown_number():
    assert find_customer(Session(), "9999999999")["found"] is False


def test_loan_blocked_before_verification():
    s = Session()
    find_customer(s, "9000000001")
    assert get_loan_status(s) == {"error": "not_verified"}


def test_verify_then_loan_status():
    s = verified_session("9000000001", "1990-05-14")
    assert get_loan_status(s)["emi_amount"] == 4500


def test_wrong_dob_rejected():
    s = Session()
    find_customer(s, "9000000001")
    assert verify_identity(s, "2000-01-01")["verified"] is False


def test_lockout_after_three_wrong_attempts():
    s = Session()
    find_customer(s, "9000000001")
    for _ in range(3):
        verify_identity(s, "2000-01-01")
    assert verify_identity(s, "1990-05-14").get("locked") is True
    assert get_loan_status(s) == {"error": "not_verified"}


def test_similar_names_do_not_mix():
    a = verified_session("9000000001", "1990-05-14")
    b = verified_session("9000000005", "1991-01-09")
    assert get_loan_status(a)["name"] == "Rahul Sharma"
    assert get_loan_status(b)["name"] == "Rahul Sarma"


def test_payment_in_reconciliation_is_visible():
    s = verified_session("9000000004", "1993-07-30")
    assert get_loan_status(s)["payment_in_reconciliation"]["amount"] == 3200


def test_closed_loan():
    s = verified_session("9000000009", "1996-04-11")
    assert get_loan_status(s)["status"] == "closed"


def test_invalid_outcome_rejected():
    assert "error" in record_outcome(Session(), "made_up")


def test_transfer_sets_outcome():
    s = Session()
    transfer_to_human(s, "customer asked")
    assert s.outcome == "transferred"


def test_mock_data_is_sane():
    customers = _customers()
    assert len({c["id"] for c in customers}) == len(customers)
    assert len({c["phone"] for c in customers}) == len(customers)