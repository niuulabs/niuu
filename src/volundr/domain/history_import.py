"""Errors shared by native-history import services and repository adapters."""


class HistoryImportError(Exception):
    """A native transcript could not be imported safely."""


class HistoryImportConflictError(HistoryImportError):
    """The target is live, already populated, or belongs to another import."""


class HistoryImportSessionNotFoundError(HistoryImportError):
    """The target session no longer exists."""


class HistoryImportValidationError(HistoryImportError, ValueError):
    """The source identity or normalized frames are invalid."""
