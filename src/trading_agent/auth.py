"""OAuth2 refresh-token grant for tastytrade. Fails closed.

Secrets come from SSM Parameter Store (/trading-agent/<env>/...) on EC2, or
from environment variables for local development. They are held in memory
only and never printed: Secret.__repr__ masks the value.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import requests

BASE_URLS = {
    "sandbox": "https://api.cert.tastyworks.com",
    "production": "https://api.tastyworks.com",
}
USER_AGENT = "trading-agent/0.1.0"
REFRESH_MARGIN_S = 60


class AuthError(RuntimeError):
    pass


class Secret:
    __slots__ = ("_v",)

    def __init__(self, value: str) -> None:
        self._v = value

    def get(self) -> str:
        return self._v

    def __repr__(self) -> str:
        return "Secret(***)"

    __str__ = __repr__

    def __bool__(self) -> bool:
        return bool(self._v)


@dataclass
class Credentials:
    client_id: Secret
    client_secret: Secret
    refresh_token: Secret
    account: Secret
    save_refresh_token: Callable[[str], None] | None = field(default=None, repr=False)


def _ssm_loader(env: str, region: str | None) -> Credentials:
    try:
        import boto3  # type: ignore[import-not-found]
    except ImportError as e:  # pragma: no cover - depends on host
        raise AuthError("TA_SECRETS=ssm needs boto3 (pip install boto3)") from e
    ssm = boto3.client("ssm", region_name=region)
    prefix = f"/trading-agent/{env}/"
    names = [prefix + n for n in ("client_id", "client_secret", "refresh_token", "account")]
    resp = ssm.get_parameters(Names=names, WithDecryption=True)
    values = {p["Name"].rsplit("/", 1)[1]: p["Value"] for p in resp["Parameters"]}
    missing = [n.rsplit("/", 1)[1] for n in names if n.rsplit("/", 1)[1] not in values]
    if missing:
        raise AuthError(f"missing SSM parameters under {prefix}: {missing}")

    def save(token: str) -> None:
        ssm.put_parameter(Name=prefix + "refresh_token", Value=token, Type="SecureString", Overwrite=True)

    return Credentials(*(Secret(values[k]) for k in ("client_id", "client_secret", "refresh_token", "account")),
                       save_refresh_token=save)


def load_credentials(env: str, environ: dict[str, str] | None = None, region: str | None = None) -> Credentials:
    e = os.environ if environ is None else environ
    if e.get("TA_SECRETS", "env") == "ssm":
        return _ssm_loader(env, region)
    vals = {k: e.get(k, "") for k in ("TT_CLIENT_ID", "TT_CLIENT_SECRET", "TT_REFRESH_TOKEN", "TT_ACCOUNT")}
    missing = [k for k, v in vals.items() if not v]
    if missing:
        raise AuthError(f"missing credentials: {missing} (set in .env or use TA_SECRETS=ssm)")
    return Credentials(Secret(vals["TT_CLIENT_ID"]), Secret(vals["TT_CLIENT_SECRET"]),
                       Secret(vals["TT_REFRESH_TOKEN"]), Secret(vals["TT_ACCOUNT"]))


class TokenManager:
    """Mints 15-minute access tokens from the refresh token, refreshing early."""

    def __init__(self, env: str, creds: Credentials, session: requests.Session | None = None,
                 clock: Callable[[], float] = time.time) -> None:
        if env not in BASE_URLS:
            raise AuthError(f"no tastytrade API for env {env!r}")
        self.base_url = BASE_URLS[env]
        self.creds = creds
        self.session = session or requests.Session()
        self.clock = clock
        self._token: Secret | None = None
        self._expires_at = 0.0

    @property
    def account(self) -> str:
        return self.creds.account.get()

    def valid(self) -> bool:
        return self._token is not None and self.clock() < self._expires_at - REFRESH_MARGIN_S

    def access_token(self) -> str:
        if not self.valid():
            self._refresh()
        assert self._token is not None
        return self._token.get()

    def _refresh(self) -> None:
        body = {
            "grant_type": "refresh_token",
            "refresh_token": self.creds.refresh_token.get(),
            "client_secret": self.creds.client_secret.get(),
        }
        try:
            r = self.session.post(f"{self.base_url}/oauth/token", data=body,
                                  headers={"User-Agent": USER_AGENT, "Accept": "application/json"}, timeout=15)
        except requests.RequestException as e:
            self._token = None
            raise AuthError(f"token request failed: {type(e).__name__}") from None
        if r.status_code != 200:
            self._token = None
            raise AuthError(f"token refresh rejected: HTTP {r.status_code}")
        data: dict[str, Any] = r.json()
        token = data.get("access_token")
        if not token:
            raise AuthError("token response had no access_token")
        self._token = Secret(token)
        self._expires_at = self.clock() + float(data.get("expires_in", 900))
        rotated = data.get("refresh_token")
        if rotated and rotated != self.creds.refresh_token.get():
            self.creds.refresh_token = Secret(rotated)
            if self.creds.save_refresh_token:
                self.creds.save_refresh_token(rotated)

    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.access_token()}",
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
