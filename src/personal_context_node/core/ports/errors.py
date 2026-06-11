from __future__ import annotations


class PortError(Exception):
    """Base class for replaceable boundary adapter failures."""


class RetryablePortError(PortError):
    """Transient adapter failure; the task can be retried."""


class TerminalPortError(PortError):
    """Permanent adapter contract or input failure; retrying unchanged is not useful."""
