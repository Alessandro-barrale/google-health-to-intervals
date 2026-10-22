"""Exceptions shared by all modules. Each one maps to a CLI exit code."""


class GhtiError(Exception):
    """Base class: a problem we can explain to the user in one message."""

    exit_code = 1


class ConfigError(GhtiError):
    """A setting is missing or invalid."""

    exit_code = 2


class AuthRequired(GhtiError):
    """There is no usable Google token: the user must (re)authorize."""

    exit_code = 3


class AuthError(GhtiError):
    """The OAuth exchange with Google failed."""

    exit_code = 3


class GoogleHealthError(GhtiError):
    """The Google Health API returned an error."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class IntervalsError(GhtiError):
    """intervals.icu returned an error."""
