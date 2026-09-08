import time
import random
import traceback
import sys
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from db.connection import execute_query

from utils.rss_feed_parser import get_feed_data
from db.config import get_sources_db_path, get_tracking_db_path
from db.feeds import (
    get_active_feeds,
    count_active_feeds,
    get_feed_tracking_info,
    update_feed_tracking,
    store_feed_entries,
    update_tracking_info,
    mark_source_feed_crawled,
)


# ----------------------------
# DATE PARSING HELPER
# ----------------------------
def _parse_date(date_str):
    """Parse RSS date string, trying RFC 822 first, then ISO 8601."""
    if not date_str:
        return None

    # Try RFC 822 format (e.g. "Wed, 02 Jul 2026 10:00:00 GMT") - most common in RSS
    try:
        dt = parsedate_to_datetime(date_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (TypeError, ValueError):
        pass

    # Fall back to ISO 8601 format (e.g. "2026-07-05T10:00:00Z")
    try:
        dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, AttributeError):
        pass

    print(f"[WARN] Could not parse date string: {date_str!r}")
    return None


# ----------------------------
# DATE FILTER (TODAY / RECENT)
# ----------------------------
def is_recent_news(date_str, hours=24):
    dt = _parse_date(date_str)
    if dt is None:
        return False
    now = datetime.now().astimezone()
    dt = dt.astimezone(now.tzinfo)
    return (now - dt).total_seconds() <= hours * 3600


def is_today(date_str):
    dt = _parse_date(date_str)
    if dt is None:
        return False
    now = datetime.now().astimezone()
    return dt.astimezone(now.tzinfo).date() == now.date()


# ----------------------------
# MAIN PIPELINE
# ----------------------------
def fetch_and_process_feeds(
    sources_db_path=None,
    tracking_db_path=None,
    delay_between_feeds=2,
    batch_size=100
):
    if sources_db_path is None:
        sources_db_path = get_sources_db_path()
    if tracking_db_path is None:
        tracking_db_path = get_tracking_db_path()

    total_feeds = count_active_feeds(sources_db_path)

    stats = {
        "total_feeds": total_feeds,
        "processed_feeds": 0,
        "new_entries": 0,
        "unchanged_feeds": 0,
        "failed_feeds": 0,
    }

    offset = 0

    while True:
        feeds = get_active_feeds(
            sources_db_path,
            limit=batch_size,
            offset=offset
        )

        if not feeds:
            break

        try:
            update_tracking_info(tracking_db_path, feeds)
        except Exception:
            print("Warning: tracking update failed")
            print(traceback.format_exc())

        for feed in feeds:
            feed_id = feed.get("id")
            source_id = feed.get("source_id")
            feed_url = feed.get("feed_url")

            try:
                tracking_info = get_feed_tracking_info(tracking_db_path, feed_id) or {}

                etag = tracking_info.get("last_etag")
                modified = tracking_info.get("last_modified")
                last_hash = tracking_info.get("entry_hash")

                feed_data = get_feed_data(
                    feed_url,
                    etag=etag,
                    modified=modified
                )

                # ----------------------------
                # VALIDATION
                # ----------------------------
                if not feed_data or not feed_data.get("is_rss_feed"):
                    stats["failed_feeds"] += 1
                    continue

                if feed_data.get("status") == 304:
                    update_feed_tracking(
                        tracking_db_path,
                        feed_id,
                        etag,
                        modified,
                        last_hash
                    )
                    mark_source_feed_crawled(sources_db_path, feed_id)
                    stats["processed_feeds"] += 1
                    stats["unchanged_feeds"] += 1
                    continue

                current_hash = feed_data.get("current_hash")

                if last_hash and current_hash == last_hash:
                    update_feed_tracking(
                        tracking_db_path,
                        feed_id,
                        feed_data.get("etag") or etag,
                        feed_data.get("modified") or modified,
                        current_hash
                    )
                    mark_source_feed_crawled(sources_db_path, feed_id)
                    stats["processed_feeds"] += 1
                    stats["unchanged_feeds"] += 1
                    continue

                parsed_entries = feed_data.get("parsed_entries") or []

                # DEBUG: show a sample raw date so we can confirm format
                if parsed_entries:
                    print(f"[DEBUG] sample date from {feed_url}: {parsed_entries[0].get('published_date')!r}")

                # ----------------------------
                # 🔥 FILTER: ONLY TODAY / RECENT NEWS
                # ----------------------------
                filtered_entries = [
                    entry for entry in parsed_entries
                    if is_today(entry.get("published_date")) or is_recent_news(entry.get("published_date"))
                ]

                # sort newest first
                filtered_entries.sort(
                    key=lambda x: x.get("published_date") or "",
                    reverse=True
                )

                # ----------------------------
                # STORE ONLY FILTERED DATA
                # ----------------------------
                if filtered_entries:
                    new_entries = store_feed_entries(
                        tracking_db_path,
                        feed_id,
                        source_id,
                        filtered_entries
                    )
                    stats["new_entries"] += new_entries
                    print(f"[NEW] {new_entries} today/news entries from {feed_url}")

                update_feed_tracking(
                    tracking_db_path,
                    feed_id,
                    feed_data.get("etag"),
                    feed_data.get("modified"),
                    current_hash
                )
                mark_source_feed_crawled(sources_db_path, feed_id)

                stats["processed_feeds"] += 1

            except Exception:
                stats["failed_feeds"] += 1
                print(f"[ERROR] feed: {feed_url}")
                print(traceback.format_exc())

            time.sleep(random.uniform(1, max(1, delay_between_feeds)))

        offset += batch_size

    return stats


# ----------------------------
# STATS
# ----------------------------
def print_stats(stats):
    print("\n===== Feed Processing Stats =====")
    print(f"Total feeds      : {stats['total_feeds']}")
    print(f"Processed feeds  : {stats['processed_feeds']}")
    print(f"Unchanged feeds  : {stats['unchanged_feeds']}")
    print(f"Failed feeds     : {stats['failed_feeds']}")
    print(f"New entries      : {stats['new_entries']}")


def latest_article_is_recent(tracking_db_path=None, max_age_hours=30):
    """Return freshness based on when the app crawled an article, not its publish date.

    News feeds can legitimately publish no articles after midnight. A 30-hour
    window avoids a false stale alert overnight while still detecting a missed
    daily refresh.
    """
    if tracking_db_path is None:
        tracking_db_path = get_tracking_db_path()

    row = execute_query(
        tracking_db_path,
        """
        SELECT MAX(datetime(crawled_date)) AS latest_crawled_date
        FROM crawled_articles
        WHERE url IS NOT NULL AND url != ''
        """,
        fetch=True,
        fetch_one=True,
    )
    latest_crawled_date = row.get("latest_crawled_date") if row else None
    if not latest_crawled_date:
        return False, None

    latest = datetime.fromisoformat(latest_crawled_date)
    now = datetime.now().astimezone()
    if latest.tzinfo is None:
        latest = latest.replace(tzinfo=now.tzinfo)
    else:
        latest = latest.astimezone(now.tzinfo)
    age_seconds = (now - latest).total_seconds()
    return 0 <= age_seconds <= max_age_hours * 3600, latest_crawled_date


def refresh_articles_from_feeds():
    tracking_db_path = get_tracking_db_path()
    stats = fetch_and_process_feeds(tracking_db_path=tracking_db_path)
    print_stats(stats)

    if stats["total_feeds"] == 0:
        print("WARNING: No active RSS feeds are configured; skipping article refresh.")
        return 0

    if stats["total_feeds"] > 0 and stats["processed_feeds"] == 0 and stats["failed_feeds"] == stats["total_feeds"]:
        print("ERROR: all active feeds failed; no articles were refreshed.")
        return 1

    if stats["new_entries"] > 0:
        print("\nStarting URL crawl for newly discovered feed entries...")
        from processors.url_processor import crawl_in_batches, print_stats as print_crawl_stats

        crawl_stats = crawl_in_batches(
            tracking_db_path=tracking_db_path,
            batch_size=20,
            total_batches=50,
            delay_between_batches=10,
        )
        print_crawl_stats(crawl_stats)

        if crawl_stats["success_count"] == 0:
            print("WARNING: feed entries were found, but this run did not crawl any article URLs successfully.")

    is_fresh, latest_crawled_date = latest_article_is_recent(tracking_db_path)
    if not is_fresh:
        print(f"ERROR: latest crawled article is older than 30 hours. Latest crawled: {latest_crawled_date}")
        return 1

    print(f"Freshness check passed. Latest crawled article: {latest_crawled_date}")
    return 0


if __name__ == "__main__":
    sys.exit(refresh_articles_from_feeds())
