import argparse
import json
import logging
import sys

from src.dal.db import DEFAULT_DB_PATH, init_db
from src.dal.models import QUOTA_DAILY_DEFAULT, QUOTA_RESERVE, VideoStatus
from src.publisher.queue import (
    get_quota_used,
    get_video,
    list_videos,
    mark_failed,
    mark_rendered,
)
from src.publisher.scheduler import seed_slots, tick


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def main() -> None:
    parser = argparse.ArgumentParser(prog="src.publisher")
    parser.add_argument("-v", "--verbose", action="store_true")

    sub = parser.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init", help="Create the SQLite schema")
    p_init.add_argument("--db", default=str(DEFAULT_DB_PATH))

    p_seed = sub.add_parser("seed", help="Populate upcoming upload slots")
    p_seed.add_argument("--days", type=int, default=7)
    p_seed.add_argument("--db", default=str(DEFAULT_DB_PATH))

    p_tick = sub.add_parser("tick", help="Run one scheduler pass (cron entry)")
    p_tick.add_argument("--privacy", default="unlisted",
                        choices=["private", "unlisted", "public"])
    p_tick.add_argument("--quota", type=int, default=QUOTA_DAILY_DEFAULT)
    p_tick.add_argument("--reserve", type=int, default=QUOTA_RESERVE)
    p_tick.add_argument("--dry-run", action="store_true")
    p_tick.add_argument("--db", default=str(DEFAULT_DB_PATH))

    p_list = sub.add_parser("list", help="List videos")
    p_list.add_argument("--status", default=None,
                        choices=[s.value for s in VideoStatus])
    p_list.add_argument("--limit", type=int, default=100)
    p_list.add_argument("--db", default=str(DEFAULT_DB_PATH))

    p_show = sub.add_parser("show", help="Show one video")
    p_show.add_argument("job_id")
    p_show.add_argument("--db", default=str(DEFAULT_DB_PATH))

    p_skip = sub.add_parser("skip", help="Mark a video SKIPPED so it never uploads")
    p_skip.add_argument("job_id")
    p_skip.add_argument("--db", default=str(DEFAULT_DB_PATH))

    p_quota = sub.add_parser("quota", help="Show today's quota usage")
    p_quota.add_argument("--db", default=str(DEFAULT_DB_PATH))

    args = parser.parse_args()
    _setup_logging(args.verbose)

    if args.cmd == "init":
        init_db(args.db)
        print(f"OK: {args.db}")

    elif args.cmd == "seed":
        n = seed_slots(days=args.days, db_path=args.db)
        print(f"Seeded {n} slots")

    elif args.cmd == "tick":
        result = tick(
            privacy=args.privacy,
            quota_daily=args.quota,
            reserve=args.reserve,
            db_path=args.db,
            dry_run=args.dry_run,
        )
        print(json.dumps(result, indent=2, default=str))

    elif args.cmd == "list":
        status = VideoStatus(args.status) if args.status else None
        rows = list_videos(status=status, limit=args.limit, db_path=args.db)
        for r in rows:
            print(f"{r['created_at']}  {r['status']:<9}  {r['job_id']}  {r['topic']}")

    elif args.cmd == "show":
        print(json.dumps(get_video(args.job_id, db_path=args.db),
                         indent=2, default=str))

    elif args.cmd == "skip":
        from datetime import datetime, timezone
        from src.dal.db import get_conn
        with get_conn(args.db) as conn:
            conn.execute(
                "UPDATE videos SET status=?, updated_at=? WHERE job_id=?",
                (VideoStatus.SKIPPED, datetime.now(timezone.utc).isoformat(), args.job_id),
            )
        print(f"Marked {args.job_id} as SKIPPED")

    elif args.cmd == "quota":
        used = get_quota_used(args.db)
        print(f"Units used today: {used} / {QUOTA_DAILY_DEFAULT} "
              f"({used / QUOTA_DAILY_DEFAULT * 100:.1f}%)")
        remaining_uploads = (QUOTA_DAILY_DEFAULT - QUOTA_RESERVE - used) // 1600
        print(f"Uploads remaining today: {max(remaining_uploads, 0)}")


if __name__ == "__main__":
    main()