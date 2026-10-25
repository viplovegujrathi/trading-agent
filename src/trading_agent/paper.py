"""Paper-money broker: simulated $ account, fills at the limit price, no network.
State persists to state/paper_account.json. Same interface as Broker."""
from __future__ import annotations

import json
from pathlib import Path

from .journal import ROOT


def _strike(symbol: str) -> float:
    return int(symbol[-8:]) / 1000


def _strike_key(symbol: str) -> str:
    return symbol


class PaperBroker:
    def __init__(self, start_cash: float = 5000.0, root: Path = ROOT):
        self.path = root / "state" / "paper_account.json"
        if self.path.exists():
            self.acct = json.loads(self.path.read_text())
        else:
            self.acct = {"start_cash": start_cash, "cash": start_cash, "reserved": 0.0, "orders": {}, "held": {}}
            self._save()

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.acct, indent=2, sort_keys=True))

    @property
    def available_cash(self) -> float:
        return round(self.acct["cash"] - self.acct["reserved"], 2)

    @staticmethod
    def collateral(order: dict) -> float:
        def wing(kind: str) -> float:
            k = [_strike(l["symbol"]) for l in order["legs"] if l["symbol"][-9] == kind]
            return max(k) - min(k) if len(k) == 2 else 0.0
        qty = order["legs"][0]["quantity"]
        return round((max(wing("P"), wing("C")) - float(order["price"])) * 100 * qty, 2)

    def dry_run(self, order: dict) -> dict:
        if order["price-effect"] == "Debit":
            return {"bp_effect": 0.0, "warnings": []}
        bp = self.collateral(order)
        warn = [] if self.available_cash >= bp else ["insufficient buying power"]
        return {"bp_effect": bp, "warnings": warn}

    def submit(self, order: dict) -> dict:
        ext = order["external-identifier"]
        if ext in self.acct["orders"]:                      # idempotent
            return self.acct["orders"][ext]
        qty = order["legs"][0]["quantity"]
        key = "|".join(sorted(_strike_key(l["symbol"]) for l in order["legs"]))
        if order["price-effect"] == "Debit":                # closing order
            held = self.acct["held"].pop(key, None)
            if held is None:
                rec = {"status": "Rejected", "reason": "no such position", "external-identifier": ext}
            else:
                self.acct["cash"] = round(self.acct["cash"] - float(order["price"]) * 100 * qty, 2)
                self.acct["reserved"] = round(self.acct["reserved"] - held, 2)
                rec = {"status": "Filled", "fill_price": order["price"], "external-identifier": ext}
        else:
            bp = self.collateral(order)
            if self.available_cash < bp:
                rec = {"status": "Rejected", "external-identifier": ext}
            else:
                self.acct["cash"] = round(self.acct["cash"] + float(order["price"]) * 100 * qty, 2)
                self.acct["reserved"] = round(self.acct["reserved"] + bp, 2)
                self.acct["held"][key] = bp
                rec = {"status": "Filled", "fill_price": order["price"], "collateral": bp,
                       "external-identifier": ext}
        self.acct["orders"][ext] = rec
        self._save()
        return rec

    def find_order(self, external_identifier: str) -> dict | None:
        return self.acct["orders"].get(external_identifier)
