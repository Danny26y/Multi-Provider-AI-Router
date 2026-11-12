"""Legacy manual command; use scripts/demo.py for configurable demonstrations.

Collection/import never starts services or calls a provider. Direct invocation
runs an authenticated, deterministic loopback fallback demonstration and exits.
"""

import subprocess
import sys
from pathlib import Path


def run_test() -> int:
    script = Path(__file__).resolve().parent / "scripts" / "demo.py"
    return subprocess.call([sys.executable, str(script), "--scenario", "fallback", "--verify-only"])


if __name__ == "__main__":
    raise SystemExit(run_test())
