class NotConfigured(RuntimeError):
    """A required integration or secret is missing. Raised instead of mocking the integration."""


class NotSupported(RuntimeError):
    """The connector does not support this operation."""
