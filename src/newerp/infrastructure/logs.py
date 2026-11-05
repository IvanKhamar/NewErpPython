"""Logging setup for the API process.

Python's default level is WARNING and uvicorn sets up only its own loggers, so audit.info(...) would go nowhere. Only
the audit logger is turned on: its lines hold ids and amounts, while other INFO lines may name a phone number.
"""

import logging

AUDIT_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"


def enable_audit_log() -> None:
    audit = logging.getLogger("newerp.audit")
    audit.setLevel(logging.INFO)
    if not audit.handlers:
        handler = logging.StreamHandler()  # stderr: the container's log, like Console logging in .NET
        handler.setFormatter(logging.Formatter(AUDIT_FORMAT))
        audit.addHandler(handler)
