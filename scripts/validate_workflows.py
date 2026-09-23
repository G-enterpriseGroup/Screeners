"""Validate active GitHub workflow YAML before GitHub rejects it without logs.

Run with PyYAML installed: python scripts/validate_workflows.py
Optional file arguments support checking proposed workflows outside the repo.
Historical archived workflows are intentionally not active validation targets.
"""
from pathlib import Path
import sys

import yaml

ROOT = Path(__file__).resolve().parents[1]


def validate(path: Path) -> None:
    # BaseLoader preserves GitHub's `on` key; YAML 1.1 SafeLoader interprets it
    # as a boolean. This checks syntax/shape, not the full Actions schema.
    config = yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    if not isinstance(config, dict) or not config.get("on"):
        raise ValueError("Workflow must declare an event in 'on'")
    if not isinstance(config.get("jobs"), dict) or not config["jobs"]:
        raise ValueError("Workflow must declare a nonempty jobs mapping")


def main() -> int:
    paths = [Path(p) for p in sys.argv[1:]] if sys.argv[1:] else sorted(
        (ROOT / ".github/workflows").glob("*.y*ml")
    )
    failures = []
    if not paths:
        print("WORKFLOW YAML: FAIL (no active workflow files found)")
        return 1
    for path in paths:
        try:
            validate(path)
        except (OSError, ValueError, yaml.YAMLError) as exc:
            failures.append(f"{path}: {exc}")
    if failures:
        print("WORKFLOW YAML: FAIL\n" + "\n".join(failures))
        return 1
    print(f"WORKFLOW YAML: PASS ({len(paths)} files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
