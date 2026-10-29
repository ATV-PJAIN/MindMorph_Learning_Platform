"""Safe errors shared by storage, HTTP and streaming boundaries."""


class StorageUnavailableError(RuntimeError):
    pass


class SessionConflictError(RuntimeError):
    pass


class SessionSnapshot(dict):
    """A JSON-compatible session with an out-of-band optimistic-lock version."""

    def __init__(self, data, revision):
        super().__init__(data)
        self.revision = revision
