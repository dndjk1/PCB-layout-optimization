from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def run_step(args: list[str]) -> None:
    command = [sys.executable, *args]
    print("$ " + " ".join(command), flush=True)
    subprocess.run(command, cwd=ROOT, check=True)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    run_step(["scripts/week1_inspect.py"])
    run_step(["scripts/visualize_initial_layouts.py"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
