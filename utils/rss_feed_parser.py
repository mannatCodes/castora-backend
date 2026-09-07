import feedparser
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
from typing import List, Dict, Any, Optional


# ----------------------------
# HASH GENERATION
# ----------------------------
def get_hash(entries: List[Dict[str, str]]) -> str:
    texts = ""

    for entry in entries:
        texts += (
            str(entry.get("entry_id", "")) +
            str(entry.get("title", "")) +
            str(entry.get("published_date", ""))
        )

    return hashlib.md5(texts.encode("utf-8")).hexdigest()


# ----------------------------
# DATE NORMALIZATION (ROBUST)
# ----------------------------
def normalize_published_date(value: Any) -> str:
    if not value:
        return datetime.now(timezone.utc).isoformat()

    try:
        dt = None

        # Case 1: ISO string
        if isinstance(value, str):
            try:
                dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except Exception:
                dt = parsedate_to_datetime(value)

        # Case 2: struct_time from feedparser
        elif hasattr(value, "tm_year"):
            dt = datetime(
                value.tm_year,
                value.tm_mon,
                value.tm_mday,
                value.tm_hour,
                value.tm_min,
                value.tm_sec,
                tzinfo=timezone.utc,
            )

        if dt is None:
            raise ValueError("Invalid date format")

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)

        return dt.astimezone(timezone.utc).isoformat()

    except Exception:
        # fallback (never break pipeline)
        return datetime.now(timezone.utc).isoformat()


# ----------------------------
# PARSE ENTRIES
# ----------------------------
def parse_feed_entries(entries: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    parsed_entries = []

    for entry in entries:
        content = entry.get("content")
        if isinstance(content, list) and content:
            content = content[0].get("value", "")
        else:
            content = content or entry.get("description", "")

        published = normalize_published_date(
            entry.get("published_parsed")
            or entry.get("updated_parsed")
            or entry.get("published")
            or entry.get("updated")
            or entry.get("pubDate")
            or entry.get("created")
        )

        parsed_entries.append({
            "title": entry.get("title", ""),
            "link": entry.get("link", ""),
            "summary": entry.get("summary", ""),
            "content": content,
            "published_date": published,
            "entry_id": entry.get("id") or entry.get("link", ""),
        })

    return parsed_entries


# ----------------------------
# FIXED RSS VALIDATION (IMPORTANT)
# ----------------------------
def is_rss_feed(feed_data) -> bool:
    if not feed_data:
        return False

    # Some live feeds set bozo because of minor XML/header issues while still
    # returning valid entries. Treat entries as the source of truth.
    entries = getattr(feed_data, "entries", None)
    return bool(entries)


# ----------------------------
# MAIN FETCH FUNCTION
# ----------------------------
def get_feed_data(
    feed_url: str,
    etag: Optional[str] = None,
    modified: Optional[Any] = None
) -> Dict[str, Any]:

    feed_data = feedparser.parse(feed_url, etag=etag, modified=modified)

    status = getattr(feed_data, "status", None)
    etag = getattr(feed_data, "etag", None)
    modified = getattr(feed_data, "modified", None)

    if getattr(feed_data, "bozo", 0):
        exception = getattr(feed_data, "bozo_exception", None)
        print(f"[WARN] feedparser warning for {feed_url}: {exception}")

    if status == 304:
        return {
            "is_rss_feed": True,
            "parsed_entries": [],
            "modified": modified,
            "status": status,
            "current_hash": None,
            "etag": etag,
        }

    # Handle feeds that returned no parseable entry container.
    if not is_rss_feed(feed_data):
        return {
            "is_rss_feed": False,
            "parsed_entries": [],
            "modified": None,
            "status": status,
            "current_hash": None,
            "etag": None,
        }

    entries = feed_data.get("entries", [])

    parsed_entries = parse_feed_entries(entries)

    current_hash = get_hash(parsed_entries) if parsed_entries else None

    return {
        "is_rss_feed": True,
        "parsed_entries": parsed_entries,
        "modified": modified,
        "status": status,
        "current_hash": current_hash,
        "etag": etag,
    }
