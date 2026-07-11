class DashboardError(Exception):
    """Base error for dashboard generation."""


class DashboardPersistenceError(DashboardError):
    """Raised when dashboard persistence inputs are unavailable."""
