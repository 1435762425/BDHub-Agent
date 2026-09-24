#!/usr/bin/env python3
"""Plan, apply or restore the run-history retention policy (current + two recent versions).

``plan`` is read-only.  ``apply --confirm`` archives every candidate outside the repository,
verifies the archive and only then removes the originals; run it while workers are paused or idle.
``restore --archive <dir> --confirm`` re-inserts missing rows and files from one archive.
"""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "scripts"))

from lib.state_retention import RetentionError, apply, plan, restore  # noqa: E402

DEFAULT_ARCHIVE_ROOT = ROOT.parent / f"{ROOT.name}-backups" / "retention"


def _summary(result):
    return {"catalogSnapshots": len(result["catalogSnapshots"]),
            "catalogSnapshotBytes": sum(row["bytes"] for row in result["catalogSnapshots"]),
            "sourceRuns": {source["database"]: {"archive": source["candidates"],
                                                "rows": sum(source["rows"].values()),
                                                "protected": source["protected"]}
                           for source in result["sourceRuns"]},
            **{key: len(result[key]) for key in ("catalogFiles", "reports", "stageLogs", "rotateLogs",
                                                  "identityGenerations")},
            "identityKept": result["identityKept"], "openWorkflowRuns": len(result["openWorkflowRuns"])}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("plan", "apply", "restore"))
    parser.add_argument("--archive-root", type=Path, default=DEFAULT_ARCHIVE_ROOT)
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--confirm", action="store_true")
    parser.add_argument("--vacuum", action="store_true")
    parser.add_argument("--full", action="store_true", help="plan: print every candidate, not only counts")
    args = parser.parse_args(argv)
    try:
        if args.action == "plan":
            result = plan(ROOT)
            output = result if args.full else _summary(result)
        elif not args.confirm:
            raise RetentionError("state_retention_confirmation_required")
        elif args.action == "apply":
            output = apply(ROOT, args.archive_root, vacuum=args.vacuum)
        else:
            if args.archive is None:
                raise RetentionError("state_retention_archive_required")
            output = restore(ROOT, args.archive)
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 0
    except (RetentionError, OSError, ValueError) as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
