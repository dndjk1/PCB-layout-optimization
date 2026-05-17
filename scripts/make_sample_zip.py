from __future__ import annotations

import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    source_dir = ROOT / "data" / "small-1_original_artifacts"
    output_path = ROOT / "results" / "ui_uploads" / "sample_small-1.zip"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(source_dir.glob("*")):
            if path.suffix.lower() in {".nodes", ".nets", ".pl"}:
                archive.write(path, arcname=path.name)

    print(f"Saved: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
