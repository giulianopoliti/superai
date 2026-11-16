"""Shared durable reservation controls for optional identity providers."""

from decimal import Decimal


class OfferDecisionBudget:
    def __init__(self, *, max_calls, max_spend_usd, max_input_tokens, reserve_rate):
        if not 1 <= max_calls <= 20 or not 1 <= max_input_tokens <= 32000:
            raise ValueError("Invalid identity decision limits")
        self.max_spend_usd = Decimal(str(max_spend_usd))
        if not self.max_spend_usd.is_finite() or not 0 < self.max_spend_usd <= 1:
            raise ValueError("Invalid identity decision spend limit")
        self.max_calls = max_calls
        self.max_input_tokens = max_input_tokens
        self.reserve_usd_per_million = Decimal(str(reserve_rate))
        self.reservations = {}
        self.halted = False

    def reserve_payload(self, key, encoded):
        bound = len(encoded) + 4096
        reserve = Decimal(bound) * self.reserve_usd_per_million / 1_000_000
        spent = sum((Decimal(str(v)) for v in self.reservations.values()), Decimal(0))
        if (
            self.halted
            or key in self.reservations
            or len(self.reservations) >= self.max_calls
            or bound > self.max_input_tokens
            or spent + reserve > self.max_spend_usd
        ):
            return None
        self.reservations[key] = str(reserve)
        return bound
