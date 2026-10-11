#!/usr/bin/env python3
"""Preview or import existing TikTok Find profiles; no platform requests or sends."""
import argparse
import json
from pathlib import Path

from lib.profile_backfill import apply, preview


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("preview", "apply"))
    parser.add_argument("--runtime-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--market", action="append", choices=("it", "br", "my", "th", "uk", "de", "fr"))
    parser.add_argument("--preview-hash")
    parser.add_argument("--preview-file", type=Path)
    parser.add_argument("--confirm", action="store_true")
    args = parser.parse_args()
    var = (args.runtime_root / "var").resolve()
    if args.preview_file is not None and not args.preview_file.resolve().is_relative_to(var):
        parser.error("preview file must stay in this project's var")
    if args.command == "apply":
        manifest = json.loads(args.preview_file.read_text()) if args.preview_file else None
        if manifest is not None and manifest.get("schema") != "bdhub.profile-backfill-plan.v1":
            parser.error("invalid preview file")
        expected = args.preview_hash or (manifest or {}).get("previewHash")
        if not args.confirm or not expected:
            parser.error("apply requires a preview and --confirm")
        scope = (manifest or {}).get("markets", args.market)
        value = apply(args.runtime_root, expected, markets=scope, report_files=(manifest or {}).get("reportFiles"),
                      progress=lambda row: print(json.dumps({"progress": row}), flush=True))
    else:
        value = preview(args.runtime_root, markets=args.market)
        if args.preview_file:
            manifest = {"schema": "bdhub.profile-backfill-plan.v1", "previewHash": value["previewHash"],
                        "markets": args.market, "reportFiles": value["reportFiles"]}
            args.preview_file.parent.mkdir(parents=True, exist_ok=True)
            with args.preview_file.open("x") as stream:
                args.preview_file.chmod(0o600)
                json.dump(manifest, stream)
    print(json.dumps({k: v for k, v in value.items() if k not in {"rows", "reportFiles"}}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
