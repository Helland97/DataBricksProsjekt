"""The Databricks job must run the same package versions as the lock files (STEG5-PLAN.md section 7.2).

The job's Python packages are pinned by hand in infra/02-databricks/jobs.tf. The lock files are updated by
`uv lock` (and by Dependabot). Without this check, the two drift apart silently: tests and your laptop run one
version, the job at 06:00 another. Exits with 1 and lists every difference.

    python ci/check_pins.py
"""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JOBS_TF = ROOT / "infra" / "02-databricks" / "jobs.tf"
# Which lock file each pinned list in jobs.tf must match
LISTS = {
    "ingest_dependencies": ROOT / "ingestion" / "uv.lock",
    "dbt_dependencies": ROOT / "dbt" / "uv.lock",
}


def normalize(name: str) -> str:
    """PEP 503: names compare case-insensitively, with -, _ and . treated the same."""
    return re.sub(r"[-_.]+", "-", name).lower()


def pins_in_jobs_tf(text: str, list_name: str) -> dict[str, str]:
    """{package: version} from a list like `ingest_dependencies = [ "dlt[databricks]==1.32.0", ... ]`."""
    # The list ends at a "]" on its own line. Not the first "]": "dlt[databricks,parquet]" has one inside.
    match = re.search(rf"{list_name}\s*=\s*\[(.*?)\n\s*\]", text, re.S)
    if not match:
        raise SystemExit(f"{JOBS_TF}: no list named {list_name}")
    pins = {}
    for entry in re.findall(r'"([^"]+)"', match.group(1)):
        pin = re.fullmatch(r"([A-Za-z0-9_.-]+)(\[[^\]]*\])?==([^\s;]+)", entry)
        if not pin:
            raise SystemExit(f"{JOBS_TF}: {entry!r} in {list_name} is not pinned with == (an exact version)")
        pins[normalize(pin.group(1))] = pin.group(3)
    if not pins:
        raise SystemExit(f"{JOBS_TF}: found no packages in {list_name}, so the check would check nothing.")
    return pins


def locked_versions(lock_file: Path) -> dict[str, str]:
    lock = tomllib.loads(lock_file.read_text(encoding="utf-8"))
    return {normalize(p["name"]): p["version"] for p in lock["package"] if "version" in p}


def main() -> int:
    text = JOBS_TF.read_text(encoding="utf-8")
    problems = []
    for list_name, lock_file in LISTS.items():
        locked = locked_versions(lock_file)
        for package, version in pins_in_jobs_tf(text, list_name).items():
            if package not in locked:
                problems.append(f"{list_name}: {package}=={version} is not in {lock_file.relative_to(ROOT)}")
            elif locked[package] != version:
                problems.append(
                    f"{list_name}: {package}=={version} in jobs.tf, but {locked[package]} in "
                    f"{lock_file.relative_to(ROOT)}"
                )
    if problems:
        print("The job's pinned versions don't match the lock files:\n  " + "\n  ".join(problems))
        print("Update the versions in infra/02-databricks/jobs.tf (and terraform apply after the merge).")
        return 1
    print("The job's pinned versions match the lock files.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
