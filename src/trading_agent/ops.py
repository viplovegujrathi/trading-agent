"""EC2 ops: S3 backup and CloudWatch heartbeat via the aws CLI. Both fail soft: ops problems
must never block or crash a trading run. The runner is injectable for tests."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from .journal import ROOT

NAMESPACE = "TradingAgent"


def _aws(args: list[str]) -> bool:
    try:
        return subprocess.run(["aws", *args], capture_output=True, timeout=120).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def backup(bucket: str | None = None, root: Path = ROOT, run=_aws) -> bool:
    bucket = bucket or os.environ.get("TA_BACKUP_BUCKET")
    if not bucket:
        return False
    ok = True
    for d in ("journal", "state"):
        if (root / d).exists():
            ok &= run(["s3", "sync", str(root / d), f"s3://{bucket}/{d}/", "--only-show-errors"])
    return ok


def metric(name: str, job: str, value: float = 1, run=_aws) -> bool:
    if os.environ.get("TA_METRICS") != "1":
        return False
    return run(["cloudwatch", "put-metric-data", "--namespace", NAMESPACE, "--metric-name", name,
                "--dimensions", f"job={job}", "--value", str(value)])
