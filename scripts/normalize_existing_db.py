"""Normalize existing PostgreSQL data: preview first, then apply a saved plan; no crawling or AI calls."""
import argparse
import json
from pathlib import Path
import sys
import os

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv, dotenv_values
load_dotenv()
from app.services.db_normalization_service import DbNormalizationService
from app.core.postgres_connection import connect_postgres


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, help="Read database credentials from this file without changing project .env files")
    parser.add_argument("--host", help="Explicit database host when using separate DB_* settings")
    command = parser.add_subparsers(dest="command", required=True)
    preview = command.add_parser("preview")
    preview.add_argument("--limit", type=int, default=100)
    preview.add_argument("--after-id")
    preview.add_argument("--platform", choices=["TIKTOK", "FACEBOOK", "INSTAGRAM", "YOUTUBE"])
    preview.add_argument("--category")
    preview.add_argument("--all", action="store_true")
    apply = command.add_parser("apply")
    apply.add_argument("--plan-id", required=True)
    args = parser.parse_args()
    config = dict(dotenv_values(args.env_file)) if args.env_file else dict(os.environ)
    if args.env_file and not args.env_file.is_file():
        parser.error("Database environment file does not exist")
    if args.host:
        if config.get("NORMALIZATION_DATABASE_URL") or config.get("DATABASE_URL"):
            parser.error("Do not combine --host with a database URL; choose one complete database target")
        config["DB_HOST"] = args.host
    service = DbNormalizationService(connection_factory=lambda readonly=True:
        connect_postgres(readonly=readonly, configuration=config))
    if args.command == "preview":
        result = service.preview(limit=args.limit, after_id=args.after_id,
            platform=args.platform, category=args.category, all_rows=args.all)
    else:
        result = service.apply(args.plan_id)
    print(json.dumps({key: value for key, value in result.items() if key not in {"items", "applyResults"}}, ensure_ascii=False, indent=2))
    print(f"Full before/after report: {service._path(result['runId'])}")
    if result.get("conflictCount"):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
