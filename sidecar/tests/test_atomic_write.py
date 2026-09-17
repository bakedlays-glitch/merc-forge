"""Durability sequencing tests for the shared atomic byte writer."""
from __future__ import annotations

from pathlib import Path
import errno

import pytest

from mercwizard_core.inject import _atomic_xml


def test_posix_atomic_write_syncs_replaced_file_then_parent(tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "target.bin"
    target.write_bytes(b"old")
    calls: list[str] = []
    real_replace = _atomic_xml.os.replace

    def replace(source: str, destination: Path) -> None:
        calls.append("replace")
        real_replace(source, destination)

    monkeypatch.setattr(_atomic_xml.os, "name", "posix")
    monkeypatch.setattr(_atomic_xml.os, "replace", replace)
    monkeypatch.setattr(_atomic_xml, "_fsync_file", lambda path: calls.append(f"file:{path.name}"))
    monkeypatch.setattr(_atomic_xml, "_fsync_directory", lambda path: calls.append(f"dir:{path.name}"))

    _atomic_xml.write_bytes_atomic(target, b"new")

    assert target.read_bytes() == b"new"
    assert calls == ["replace", "file:target.bin", f"dir:{tmp_path.name}"]


def test_windows_atomic_write_uses_write_through_replacement(tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "target.bin"
    calls: list[tuple[Path, Path]] = []
    real_replace = _atomic_xml.os.replace

    def replace_write_through(source: Path, destination: Path) -> None:
        calls.append((source, destination))
        real_replace(source, destination)

    monkeypatch.setattr(_atomic_xml.os, "name", "nt")
    monkeypatch.setattr(_atomic_xml, "_replace_windows_write_through", replace_write_through)
    monkeypatch.setattr(_atomic_xml.os, "replace", lambda *_args: (_ for _ in ()).throw(AssertionError("os.replace")))

    _atomic_xml.write_bytes_atomic(target, b"new")

    assert target.read_bytes() == b"new"
    assert len(calls) == 1
    assert calls[0][1] == target


def test_directory_fsync_tolerates_only_explicit_unsupported_errors(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(_atomic_xml.os, "open", lambda *_args: (_ for _ in ()).throw(OSError(errno.EINVAL, "no dir fsync")))
    _atomic_xml._fsync_directory(tmp_path)

    monkeypatch.setattr(_atomic_xml.os, "open", lambda *_args: (_ for _ in ()).throw(OSError(errno.EIO, "disk failure")))
    with pytest.raises(OSError) as exc_info:
        _atomic_xml._fsync_directory(tmp_path)
    assert exc_info.value.errno == errno.EIO


def test_directory_fsync_propagates_io_failure_after_open(tmp_path: Path, monkeypatch) -> None:
    closed: list[int] = []
    monkeypatch.setattr(_atomic_xml.os, "open", lambda *_args: 47)
    monkeypatch.setattr(_atomic_xml.os, "fsync", lambda _fd: (_ for _ in ()).throw(OSError(errno.EIO, "disk failure")))
    monkeypatch.setattr(_atomic_xml.os, "close", lambda fd: closed.append(fd))

    with pytest.raises(OSError) as exc_info:
        _atomic_xml._fsync_directory(tmp_path)

    assert exc_info.value.errno == errno.EIO
    assert closed == [47]
