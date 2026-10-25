"""Ops: S3 backup of journal/ and state/, CloudWatch metrics (deploy.md §6-7).

Both are best-effort and never block trading logic. Without AWS configured
(local or paper runs) they are no-ops.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

NAMESPACE = "TradingAgent"


def put_metric(name: str, value: float = 1.0, job: str | None = None, region: str | None = None) -> bool:
    if not os.environ.get("TA_METRICS", "") == "cloudwatch":
        return False
    try:
        import boto3  # type: ignore[import-not-found]
    except ImportError:
        return False
    dims = [{"Name": "job", "Value": job}] if job else []
    try:
        boto3.client("cloudwatch", region_name=region).put_metric_data(
            Namespace=NAMESPACE, MetricData=[{"MetricName": name, "Value": value, "Unit": "Count", "Dimensions": dims}])
        return True
    except Exception:  # noqa: BLE001 - metrics must never break a run
        return False


def backup(journal_dir: Path, state_dir: Path, bucket: str | None) -> list[str]:
    """`aws s3 sync` both directories. Returns the commands run."""
    if not bucket:
        raise RuntimeError("set TA_BACKUP_BUCKET to back up to S3")
    if shutil.which("aws") is None:
        raise RuntimeError("aws CLI not found")
    ran = []
    for local, prefix in ((journal_dir, "journal"), (state_dir, "state")):
        if not local.exists():
            continue
        cmd = ["aws", "s3", "sync", str(local), f"s3://{bucket}/{prefix}/", "--only-show-errors"]
        subprocess.run(cmd, check=True)
        ran.append(" ".join(cmd))
    return ran
