"""ResultMessage success/failure semantics: the SDK's is_error and subtype may disagree, so check both."""


def result_message_indicates_failure(msg: object) -> bool:
    """
    Treat the result as a failure if is_error is True or subtype indicates failure (e.g. error_during_execution).

    Subtypes with clearly negated meaning (e.g. containing no_error) are not misclassified.
    """
    if getattr(msg, "is_error", False):
        return True
    st = (getattr(msg, "subtype", None) or "").lower()
    if not st:
        return False
    if "no_error" in st or "non_error" in st:
        return False
    return any(k in st for k in ("error", "fail", "abort"))
