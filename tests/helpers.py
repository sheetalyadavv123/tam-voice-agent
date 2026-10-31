"""Shared test helpers."""


def confirmed(call, words="haan ji"):
    """The caller says yes, then the AI reports them as the customer."""
    call.heard(words)
    return call.report_person("customer")
