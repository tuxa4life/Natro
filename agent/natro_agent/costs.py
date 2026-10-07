"""This month's spending on Claude and Google (voice + agent), against the monthly budget."""
from datetime import datetime

from natro_agent import log

# Natro warns once spending passes this share of the budget.
WARN_AT = 0.8


def this_month():
    return f"{datetime.now():%Y-%m}"


class Ledger:
    """Sums the "cost" and "voice_cost" of every log entry this month."""

    def __init__(self, budget):
        self.budget = budget
        self.month = this_month()
        self.spent = sum(entry.get("cost", 0) + entry.get("voice_cost", 0) for entry in log.entries(self.month))

    def add(self, amount):
        """Count new spending. Returns True if it just crossed the warning level."""
        self._new_month()
        before, self.spent = self.spent, self.spent + amount
        return before < WARN_AT * self.budget <= self.spent

    @property
    def over_budget(self):
        self._new_month()
        return self.spent >= self.budget

    def _new_month(self):
        if this_month() != self.month:
            self.month, self.spent = this_month(), 0.0
