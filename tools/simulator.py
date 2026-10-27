"""Stand-ins for the company's payment systems. Real integrations replace these later."""


def receive_payment(store, customer_id, amount, received_on, status="posted"):
    """Pretend the company's accounts system recorded a payment (received_on = 'YYYY-MM-DD')."""
    return store.add_payment(customer_id, amount, received_on, status)


def pay_link(store, customer_id):
    """Pretend the customer paid the latest payment link and the gateway confirmed it."""
    link = store.latest_link(customer_id)
    if link is None:
        return {"ok": False, "reason": "no_link"}
    paid = store.mark_link_paid(link["id"])
    return {"ok": True, "link_id": paid["id"], "amount": paid["amount"]}