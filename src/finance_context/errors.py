"""Typed failures that map to HTTP `error` codes in api.md."""


class ContextError(Exception):
    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(code if not detail else f"{code}: {detail}")


class PortError(Exception):
    """Embed/Chat/JobBus failed; pipeline degrades instead of crashing."""

    def __init__(
        self,
        message: str,
        *,
        port: str = "",
        status_code: int | None = None,
    ) -> None:
        self.port = port
        self.status_code = status_code
        super().__init__(message)
