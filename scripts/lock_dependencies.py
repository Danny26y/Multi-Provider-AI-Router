"""Reproducible as-of resolution; archive generated hash locks with each release.

Run with uv==0.13.4 available. Network/index failure is fatal; no unlocked fallback.
"""

import argparse
import subprocess
from pathlib import Path

UV_VERSION = "0.13.4"
CUTOFF = "2026-11-08T00:00:00Z"


def commands(platform: str, benchmark: bool = False) -> list[list[str]]:
    common = ["uv", "pip", "compile", "--python-version", "3.12", "--python-platform", platform,
              "--exclude-newer", CUTOFF, "--generate-hashes", "--upgrade"]
    result = [
        [*common, "requirements.txt", "--output-file", ".locks/runtime.txt"],
        [*common, "requirements-dev.txt", "requirements-dashboard.txt", "--constraint", ".locks/runtime.txt",
         "--output-file", ".locks/ci.txt"],
    ]
    if benchmark:
        result.append([
            *common, "requirements-benchmark.txt", "--constraint", ".locks/ci.txt",
            "--output-file", ".locks/benchmark.txt",
        ])
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform", default="linux", choices=("linux", "windows", "macos"))
    parser.add_argument("--benchmark", action="store_true", help="Also generate the optional benchmark hash lock.")
    args = parser.parse_args()
    if subprocess.check_output(["uv", "--version"], text=True).split()[1] != UV_VERSION:
        raise SystemExit(f"Install uv=={UV_VERSION} before regenerating locks.")
    Path(".locks").mkdir(exist_ok=True)
    for command in commands(args.platform, args.benchmark):
        subprocess.run(command, check=True, timeout=300)


if __name__ == "__main__":
    main()
