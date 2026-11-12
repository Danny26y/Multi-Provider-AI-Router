"""Validate the curated bundle as Git-staged bytes and a fresh file checkout."""

import hashlib
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = Path("benchmarks/evidence/2026-11-11-windows")


@pytest.mark.skipif(shutil.which("git") is None, reason="Git is required to validate the staged evidence bundle")
def test_evidence_survives_git_staging_and_fresh_checkout(tmp_path):
    repository = tmp_path / "repository"
    repository.mkdir()
    for name in (".gitignore", ".gitattributes"):
        shutil.copyfile(ROOT / name, repository / name)
    shutil.copytree(ROOT / BUNDLE, repository / BUNDLE)
    (repository / "audit-runtime.json").write_text("{}")
    (repository / "benchmarks/results").mkdir()
    (repository / "benchmarks/results/raw.json").write_text("{}")
    (repository / BUNDLE / "audit-unrelated.json").write_text("{}")
    env = {key: value for key, value in os.environ.items() if key not in {
        "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    }}
    env["GIT_OPTIONAL_LOCKS"] = "0"

    def git(*args):
        # Pytest's nested temporary paths can exceed Windows' legacy 260-char limit.
        # Configure this invocation only; do not modify the user's Git settings.
        return subprocess.check_output(
            ["git", "-c", "core.autocrlf=true", "-c", "core.longpaths=true", *args], cwd=repository, env=env,
        )

    git("init", "--quiet")
    git("add", "--all", "--", ".")
    staged = git("ls-files", "-z").decode().split("\0")
    assert (BUNDLE / "audit-benchmark.json").as_posix() in staged
    for excluded in ("audit-runtime.json", "benchmarks/results/raw.json", (BUNDLE / "audit-unrelated.json").as_posix()):
        assert excluded not in staged
    manifest = git("show", ":" + (BUNDLE / "SHA256SUMS").as_posix()).decode("utf-8-sig")
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    git("checkout-index", "--all", "--prefix=" + fresh.as_posix() + "/")
    for line in manifest.splitlines():
        digest, relative = line.split("  ", 1)
        path = (BUNDLE / relative).as_posix()
        assert path in staged
        assert hashlib.sha256(git("show", ":" + path)).hexdigest() == digest
        assert hashlib.sha256((fresh / path).read_bytes()).hexdigest() == digest
