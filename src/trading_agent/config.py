"""Settings (from the environment) and strategy limits (from config/strategy.yaml).

Every limit that gates an order is read from strategy.yaml here and enforced in
risk.py. Nothing else in the package hard-codes a limit.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
VALID_ENVS = ("paper", "sandbox", "production")


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class AccountCfg:
    size_usd: float = 5000
    cash_buffer_usd: float = 1000


@dataclass(frozen=True)
class PortfolioCfg:
    max_open_condors: int = 4
    max_per_ticker: int = 1
    max_per_sector: int = 2
    contracts_per_condor: int = 1
    max_orders_per_day: int = 6
    daily_loss_halt_usd: float = 500


@dataclass(frozen=True)
class ScreenCfg:
    universe: str = "sp500"
    universe_max_age_days: int = 30
    min_pct_off_52w_high: float = 15
    pct_below_50dma: tuple[float, float] = (3, 8)
    max_rsi14: float = 60
    min_open_interest_short: int = 500
    max_short_leg_spread_pct_of_mid: float = 10
    preferred_min_iv_rank: float = 25
    exclude_earnings_before_expiry: bool = True
    exclude_ex_dividend_before_expiry: bool = True


@dataclass(frozen=True)
class StructureCfg:
    dte_window: tuple[int, int] = (30, 45)
    dte_target: int = 33
    wing_widths_usd: tuple[float, ...] = (2.5, 5, 7.5, 10)
    short_put_min_otm_pct: float = 3
    short_call_min_above_price_pct: float = 5
    short_call_at_or_above_50dma: bool = True
    max_short_delta: float = 0.20
    call_wing_le_put_wing: bool = True
    min_call_side_credit: float = 0.15


@dataclass(frozen=True)
class SizingCfg:
    max_collateral_usd: float = 1000
    min_total_credit_usd: float = 100


@dataclass(frozen=True)
class PricingCfg:
    limit: str = "mid"
    reprice_after_minutes: int = 10
    reprice_step: float = 0.05
    max_reprices: int = 1
    avoid_first_last_minutes: int = 15


@dataclass(frozen=True)
class ExitsCfg:
    take_profit_pct_of_max: float = 50
    close_at_dte: int = 21
    close_on_short_strike_touch: bool = True
    close_day_before_expiry: bool = True
    stop_loss_multiple_of_credit: float = 2.0


@dataclass(frozen=True)
class ApprovalCfg:
    required: bool = True
    timeout_minutes: int = 15


@dataclass(frozen=True)
class StrategyConfig:
    account: AccountCfg = field(default_factory=AccountCfg)
    portfolio: PortfolioCfg = field(default_factory=PortfolioCfg)
    screen: ScreenCfg = field(default_factory=ScreenCfg)
    structure: StructureCfg = field(default_factory=StructureCfg)
    sizing: SizingCfg = field(default_factory=SizingCfg)
    pricing: PricingCfg = field(default_factory=PricingCfg)
    exits: ExitsCfg = field(default_factory=ExitsCfg)
    approval: ApprovalCfg = field(default_factory=ApprovalCfg)
    source: str = "defaults"

    @classmethod
    def from_dict(cls, raw: dict[str, Any], source: str = "dict") -> "StrategyConfig":
        sections = {}
        for f in fields(cls):
            if f.name == "source":
                continue
            section_cls = f.default_factory  # type: ignore[misc]
            sections[f.name] = _build(section_cls, raw.get(f.name) or {}, f.name)
        unknown = set(raw) - {f.name for f in fields(cls)}
        if unknown:
            raise ConfigError(f"unknown strategy.yaml sections: {sorted(unknown)}")
        cfg = cls(**sections, source=source)
        if not cfg.approval.required:
            # risk.md: approval stays on until Phase 5 criteria are met, and
            # relaxing it is a separate explicit decision. Not supported here.
            raise ConfigError("approval.required=false is not supported")
        return cfg


def _build(section_cls: type, raw: dict[str, Any], name: str) -> Any:
    known = {f.name: f for f in fields(section_cls)}
    unknown = set(raw) - set(known)
    if unknown:
        raise ConfigError(f"unknown keys in strategy.yaml [{name}]: {sorted(unknown)}")
    kwargs = {}
    for key, value in raw.items():
        if isinstance(value, list):
            value = tuple(value)
        kwargs[key] = value
    return section_cls(**kwargs)


def load_strategy(path: Path | None = None, env: str | None = None) -> StrategyConfig:
    """Load config/strategy.yaml. Outside production, fall back to the example file."""
    path = path or REPO_ROOT / "config" / "strategy.yaml"
    if not path.exists():
        if (env or "") == "production":
            raise ConfigError(f"{path} is required in production")
        path = REPO_ROOT / "config" / "strategy.example.yaml"
    with path.open() as fh:
        raw = yaml.safe_load(fh) or {}
    return StrategyConfig.from_dict(raw, source=str(path))


@dataclass(frozen=True)
class Settings:
    env: str
    halt_env: bool
    approval_channel: str
    state_dir: Path
    journal_dir: Path
    data_dir: Path
    aws_region: str | None

    @property
    def is_live(self) -> bool:
        return self.env == "production"


def load_settings(environ: dict[str, str] | None = None) -> Settings:
    if environ is None:
        # .env fills only what the real environment doesn't set; credentials
        # and bot settings are then read from os.environ by auth/approval.
        _load_dotenv_into(os.environ, REPO_ROOT / ".env")
        e = dict(os.environ)
    else:
        e = dict(environ)
    env = e.get("TT_ENV", "sandbox").strip().lower()
    if env not in VALID_ENVS:
        raise ConfigError(f"TT_ENV must be one of {VALID_ENVS}, got {env!r}")
    root = Path(e.get("TA_HOME", str(REPO_ROOT)))
    return Settings(
        env=env,
        halt_env=e.get("TA_HALT", "0").strip() == "1",
        approval_channel=e.get("TA_APPROVAL_CHANNEL", "cli").strip().lower(),
        state_dir=Path(e.get("TA_STATE_DIR", str(root / "state"))),
        journal_dir=Path(e.get("TA_JOURNAL_DIR", str(root / "journal"))),
        data_dir=Path(e.get("TA_DATA_DIR", str(root / "data"))),
        aws_region=e.get("AWS_REGION") or None,
    )


def _load_dotenv_into(environ: Any, path: Path) -> None:
    """Minimal .env reader. Real environment variables win over the file."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.split(" #", 1)[0].strip().strip('"').strip("'")
        environ.setdefault(key.strip(), value)
