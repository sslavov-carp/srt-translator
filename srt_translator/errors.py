class TranslationError(Exception):
    """Fatal API or configuration error."""


class MalformedResponseError(TranslationError):
    """The model returned invalid JSON, IDs, text, or formatting tags."""
