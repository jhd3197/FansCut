"""Exception hierarchy; the server maps these onto HTTP statuses."""

from __future__ import annotations


class FansCutError(Exception):
    """Base for every error FansCut raises on purpose."""


class ConfigError(FansCutError):
    pass


class NotFoundError(FansCutError):
    pass


class ClientError(FansCutError):
    """A remote service (Sonarr, Plex, a catalogue) failed or refused."""

    def __init__(self, message: str, status: int | None = None, retry_after: str | None = None):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


class FFmpegError(FansCutError):
    pass


class JobCancelled(FansCutError):
    pass
