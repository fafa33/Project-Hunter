from __future__ import annotations


class AutomationError(Exception):
    pass


class AutomationConfigurationError(AutomationError):
    pass


class AutomationLifecycleError(AutomationError):
    pass


class AutomationLockError(AutomationError):
    pass


class AutomationReplaySafetyError(AutomationError):
    pass
