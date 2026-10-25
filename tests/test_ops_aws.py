from trading_agent import ops


def test_backup_syncs_existing_dirs_only(tmp_path):
    (tmp_path / "journal").mkdir()
    calls = []
    assert ops.backup("b", tmp_path, run=lambda a: calls.append(a) or True)
    assert len(calls) == 1 and calls[0][:2] == ["s3", "sync"] and calls[0][3] == "s3://b/journal/"


def test_backup_without_bucket_is_noop(tmp_path, monkeypatch):
    monkeypatch.delenv("TA_BACKUP_BUCKET", raising=False)
    assert ops.backup(None, tmp_path, run=lambda a: True) is False


def test_backup_failure_reported_not_raised(tmp_path):
    (tmp_path / "state").mkdir()
    assert ops.backup("b", tmp_path, run=lambda a: False) is False


def test_metric_off_by_default_and_soft(monkeypatch):
    monkeypatch.delenv("TA_METRICS", raising=False)
    assert ops.metric("RunCompleted", "run-open", run=lambda a: True) is False
    monkeypatch.setenv("TA_METRICS", "1")
    seen = []
    assert ops.metric("RunFailed", "run-open", run=lambda a: seen.append(a) or True)
    assert "--namespace" in seen[0] and "job=run-open" in seen[0]


def test_aws_missing_cli_returns_false(monkeypatch):
    import subprocess
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError()))
    assert ops._aws(["s3", "ls"]) is False
