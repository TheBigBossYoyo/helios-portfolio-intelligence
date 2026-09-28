from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from helios.backup import (
    BackupError,
    backup_database,
    backup_filename_pattern,
    format_size,
)
from helios.cli import main


def _make_wal_database(path: Path) -> None:
    connection = sqlite3.connect(str(path))
    try:
        connection.execute("PRAGMA journal_mode=WAL;")
        connection.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
        connection.execute("INSERT INTO t (v) VALUES ('a')")
        connection.commit()
    finally:
        connection.close()


def _table_row_count(path: Path) -> int:
    connection = sqlite3.connect(str(path))
    try:
        return int(connection.execute("SELECT COUNT(*) FROM t").fetchone()[0])
    finally:
        connection.close()


# ---------------------------------------------------------------------------
# Online backup, including while a writer is mid-transaction
# ---------------------------------------------------------------------------


def test_backup_produces_a_verified_copy(tmp_path: Path) -> None:
    source = tmp_path / "helios.sqlite3"
    _make_wal_database(source)

    result = backup_database(source, dest_dir=tmp_path / "backups", keep=14)

    assert result.path.is_file()
    assert result.path.parent == tmp_path / "backups"
    assert result.size_bytes == result.path.stat().st_size
    assert _table_row_count(result.path) == 1


def test_backup_filename_matches_stem_and_utc_timestamp(tmp_path: Path) -> None:
    source = tmp_path / "helios.sqlite3"
    _make_wal_database(source)
    moment = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)

    result = backup_database(source, dest_dir=tmp_path / "backups", keep=14, now=moment)

    assert result.path.name == "helios-20260102T030405Z.sqlite3"
    assert backup_filename_pattern("helios").match(result.path.name)


def test_backup_succeeds_while_a_writer_holds_an_uncommitted_transaction(tmp_path: Path) -> None:
    """The online backup API must be safe against a live API/worker process under WAL."""
    source = tmp_path / "helios.sqlite3"
    _make_wal_database(source)

    writer = sqlite3.connect(str(source))
    writer.execute("PRAGMA journal_mode=WAL;")
    writer.execute("BEGIN")
    writer.execute("INSERT INTO t (v) VALUES ('b')")
    try:
        result = backup_database(source, dest_dir=tmp_path / "backups", keep=14)
    finally:
        writer.commit()
        writer.close()

    # The backup sees a consistent snapshot -- either before or after the in-flight insert --
    # never a torn or corrupt file.
    assert _table_row_count(result.path) in (1, 2)


def test_backup_refuses_a_missing_source(tmp_path: Path) -> None:
    with pytest.raises(BackupError, match="does not exist"):
        backup_database(tmp_path / "missing.sqlite3", dest_dir=tmp_path / "backups")


# ---------------------------------------------------------------------------
# Integrity check: fail loudly, remove the bad copy
# ---------------------------------------------------------------------------


def test_backup_removes_the_copy_when_integrity_check_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "helios.sqlite3"
    _make_wal_database(source)
    dest_dir = tmp_path / "backups"

    def _fail(path: Path) -> None:
        raise BackupError(f"backup at {path.name} failed its integrity check")

    monkeypatch.setattr("helios.backup._verify_integrity", _fail)

    with pytest.raises(BackupError, match="integrity check"):
        backup_database(source, dest_dir=dest_dir, keep=14)

    assert list(dest_dir.iterdir()) == []


def test_integrity_check_rejects_a_corrupt_file(tmp_path: Path) -> None:
    from helios.backup import _verify_integrity

    corrupt = tmp_path / "not-a-database.sqlite3"
    corrupt.write_bytes(b"this is not a sqlite file")

    with pytest.raises(BackupError, match="integrity check"):
        _verify_integrity(corrupt)


# ---------------------------------------------------------------------------
# Retention: only ever prunes this module's own backups of this database
# ---------------------------------------------------------------------------


def test_retention_prunes_only_this_databases_own_backups(tmp_path: Path) -> None:
    source = tmp_path / "helios.sqlite3"
    _make_wal_database(source)
    dest_dir = tmp_path / "backups"
    dest_dir.mkdir()

    # A file that merely lives alongside the backups, and another database's backup -- both must
    # survive pruning no matter how old they are or how small `keep` is.
    unrelated = dest_dir / "notes.txt"
    unrelated.write_text("do not touch")
    other_db_backup = dest_dir / "otherdb-20200101T000000Z.sqlite3"
    other_db_backup.write_bytes(b"other database backup")

    for second in range(3):
        backup_database(
            source,
            dest_dir=dest_dir,
            keep=2,
            now=datetime(2026, 1, 1, 0, 0, second, tzinfo=UTC),
        )

    own_backups = sorted(p.name for p in dest_dir.glob("helios-*.sqlite3"))
    assert len(own_backups) == 2
    assert own_backups == [
        "helios-20260101T000001Z.sqlite3",
        "helios-20260101T000002Z.sqlite3",
    ]
    assert unrelated.exists()
    assert other_db_backup.exists()


def test_retention_keep_one_drops_the_previous_backup_once_a_new_one_lands(tmp_path: Path) -> None:
    source = tmp_path / "helios.sqlite3"
    _make_wal_database(source)
    dest_dir = tmp_path / "backups"

    first = backup_database(
        source, dest_dir=dest_dir, keep=1, now=datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
    )
    second = backup_database(
        source, dest_dir=dest_dir, keep=1, now=datetime(2026, 1, 1, 0, 0, 1, tzinfo=UTC)
    )

    assert not first.path.exists()
    assert second.path.exists()


def test_backup_rejects_a_negative_keep(tmp_path: Path) -> None:
    source = tmp_path / "helios.sqlite3"
    _make_wal_database(source)

    with pytest.raises(BackupError, match="keep must be"):
        backup_database(source, dest_dir=tmp_path / "backups", keep=-1)


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("size", "expected"),
    [(500, "500 B"), (2048, "2.0 KB"), (5 * 1024 * 1024, "5.0 MB")],
)
def test_format_size(size: int, expected: str) -> None:
    assert format_size(size) == expected


# ---------------------------------------------------------------------------
# CLI wiring
# ---------------------------------------------------------------------------


def test_cli_backup_prints_path_and_size(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from helios.config import Settings

    settings = Settings(data_dir=tmp_path)
    source = settings.sqlite_path
    _make_wal_database(source)
    monkeypatch.setattr("helios.cli.load_settings", lambda: settings)
    monkeypatch.setattr("sys.argv", ["helios", "backup"])

    exit_code = main()

    out = capsys.readouterr().out
    assert exit_code == 0
    assert str(tmp_path / "backups") in out
    assert "B" in out or "KB" in out


def test_cli_backup_reports_a_missing_source_loudly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from helios.config import Settings

    settings = Settings(data_dir=tmp_path)
    monkeypatch.setattr("helios.cli.load_settings", lambda: settings)
    monkeypatch.setattr("sys.argv", ["helios", "backup"])

    exit_code = main()

    assert exit_code == 2
    assert "helios.backup.error" in capsys.readouterr().err


def test_cli_backup_accepts_dest_and_keep_overrides(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from helios.config import Settings

    settings = Settings(data_dir=tmp_path)
    _make_wal_database(settings.sqlite_path)
    custom_dest = tmp_path / "elsewhere"
    monkeypatch.setattr("helios.cli.load_settings", lambda: settings)
    monkeypatch.setattr(
        "sys.argv", ["helios", "backup", "--dest", str(custom_dest), "--keep", "3"]
    )

    exit_code = main()

    assert exit_code == 0
    assert list(custom_dest.glob("helios-*.sqlite3"))
