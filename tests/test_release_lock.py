"""Release lock validation is read-only, platform-specific, and hash-complete."""

import re
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.lock_dependencies import check_runtime_lock

ROOT = Path(__file__).resolve().parents[1]


def test_linux_runtime_artifact_contains_direct_pins_and_only_hashed_requirements():
    text = (ROOT / "requirements-linux.lock").read_text()
    assert "Linux / Python 3.12 runtime only" in text
    pins = {}
    hashes = 0
    for line in text.splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        requirement = re.fullmatch(r"([A-Za-z0-9_.-]+)==([^\s;]+) \\", line)
        if requirement:
            assert not pins or hashes > 0
            pins[requirement[1].lower().replace("_", "-")] = requirement[2]
            hashes = 0
        else:
            assert re.fullmatch(r"    --hash=sha256:[0-9a-f]{64}( \\)?", line)
            hashes += 1
    assert hashes > 0
    for line in (ROOT / "requirements.txt").read_text().splitlines():
        if line and not line.startswith("#"):
            name, version = line.split("==")
            assert pins[name.lower().replace("_", "-")] == version


def test_check_mode_accepts_matching_resolution_without_writing(tmp_path):
    path = tmp_path / "runtime.lock"
    path.write_bytes(b"reviewed")
    before = path.stat().st_mtime_ns
    check_runtime_lock(path, b"reviewed", b"reviewed")
    assert path.read_bytes() == b"reviewed"
    assert path.stat().st_mtime_ns == before


@pytest.mark.parametrize("first,second", [(b"changed", b"changed"), (b"reviewed", b"unstable")])
def test_check_mode_rejects_drift_or_nondeterminism_without_replacing_artifact(tmp_path, first, second):
    path = tmp_path / "runtime.lock"
    path.write_bytes(b"reviewed")
    with pytest.raises(SystemExit):
        check_runtime_lock(path, first, second)
    assert path.read_bytes() == b"reviewed"


def test_check_mode_rejects_missing_artifact_without_creating_it(tmp_path):
    path = tmp_path / "runtime.lock"
    with pytest.raises(SystemExit, match="missing or stale"):
        check_runtime_lock(path, b"resolved", b"resolved")
    assert not path.exists()


@pytest.mark.parametrize("mode", ["--check-runtime-lock", "--update-runtime-lock"])
@pytest.mark.parametrize("invalid", [["--platform", "windows"], ["--benchmark"]])
def test_release_modes_cannot_write_a_windows_or_development_lock(tmp_path, mode, invalid):
    artifact = tmp_path / "requirements-linux.lock"
    artifact.write_bytes(b"reviewed Linux artifact")
    result = subprocess.run(
        [sys.executable, "-B", str(ROOT / "scripts/lock_dependencies.py"), mode, *invalid],
        cwd=tmp_path, capture_output=True, timeout=10,
    )
    assert result.returncode == 2
    assert artifact.read_bytes() == b"reviewed Linux artifact"
    assert not (tmp_path / ".locks").exists()
