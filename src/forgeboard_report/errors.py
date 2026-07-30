"""Typed failures that cross forgeboard-report boundaries."""


class UsageError(ValueError):
    """An operator-supplied value is invalid."""

    def __init__(self, field: str, detail: str) -> None:
        self.field = field
        self.detail = detail
        super().__init__(f"{field}: {detail}")


class SourceUnavailableError(RuntimeError):
    """A required external or file source cannot be read."""

    def __init__(self, source: str, detail: str) -> None:
        self.source = source
        self.detail = detail
        super().__init__(f"{source}: {detail}")


class SourceInconsistentError(RuntimeError):
    """Repeated reads cannot establish one coherent source snapshot."""

    def __init__(self, source: str, detail: str) -> None:
        self.source = source
        self.detail = detail
        super().__init__(f"{source}: {detail}")


class InvalidCoreError(ValueError):
    """Required source evidence is readable but invalid or contradictory."""

    def __init__(self, source: str, detail: str) -> None:
        self.source = source
        self.detail = detail
        super().__init__(f"{source}: {detail}")


class InvalidSchemaError(InvalidCoreError):
    """A required source does not satisfy its declared schema."""


class InvalidGraphError(InvalidCoreError):
    """A graph is structurally meaningful but violates graph invariants."""


class PublicationError(RuntimeError):
    """A validated report bundle cannot be published atomically."""

    def __init__(self, stage: str, detail: str) -> None:
        self.stage = stage
        self.detail = detail
        super().__init__(f"{stage}: {detail}")


class PublicationWriteError(PublicationError):
    """A staged report artifact could not be written completely."""

    def __init__(self, detail: str) -> None:
        super().__init__("write", detail)


class PublicationFlushError(PublicationError):
    """A staged report artifact could not be durably flushed."""

    def __init__(self, detail: str) -> None:
        super().__init__("flush", detail)


class PublicationRenameError(PublicationError):
    """The complete staging directory could not be made visible."""

    def __init__(self, detail: str) -> None:
        super().__init__("rename", detail)
