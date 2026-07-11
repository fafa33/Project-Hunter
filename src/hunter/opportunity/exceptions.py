from __future__ import annotations


class OpportunityTimingError(Exception):
    pass


class InsufficientFusionInputError(OpportunityTimingError):
    pass


class ReplaySafetyError(OpportunityTimingError):
    pass
