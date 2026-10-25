"""Broker interface. The real tastytrade client is added later; FakeBroker is for tests.
Submit is idempotent by external-identifier and never resends blindly."""
from __future__ import annotations

from typing import Protocol


class Broker(Protocol):
    def dry_run(self, order: dict) -> dict: ...          # {"bp_effect": float, "warnings": [...]}
    def submit(self, order: dict) -> dict: ...
    def find_order(self, external_identifier: str) -> dict | None: ...


class SubmitUnknown(Exception):
    """Status of a submission could not be established; do not resend. Halt + alert."""


class FakeBroker:
    def __init__(self, bp_effect: float = 0.0, fail_submit_once: bool = False):
        self.orders: dict[str, dict] = {}
        self.bp_effect = bp_effect
        self.fail_submit_once = fail_submit_once
        self.submit_calls = 0

    def dry_run(self, order: dict) -> dict:
        return {"bp_effect": self.bp_effect, "warnings": []}

    def submit(self, order: dict) -> dict:
        self.submit_calls += 1
        self.orders[order["external-identifier"]] = {"status": "Working", **order}
        if self.fail_submit_once:
            self.fail_submit_once = False
            raise TimeoutError("simulated timeout after the broker accepted the order")
        return self.orders[order["external-identifier"]]

    def find_order(self, external_identifier: str) -> dict | None:
        return self.orders.get(external_identifier)


def submit_safely(broker: Broker, order: dict, sleep=lambda s: None) -> dict:
    ext = order["external-identifier"]
    try:
        return broker.submit(order)
    except (TimeoutError, ConnectionError):
        for _ in range(2):
            found = broker.find_order(ext)
            if found:
                return found                      # adopt, never resend
            sleep(5)
        raise SubmitUnknown(ext)
