"""Off-Render persistence for approved podcast data and media.

Render free instances erase their filesystem on restart.  This module uses the
Supabase Storage HTTP API directly, keeping the app free of an extra SDK and
keeping credentials server-side.
"""

import json
import os
import sqlite3
from pathlib import Path
from urllib.parse import quote

import requests

from db.config import APP_ROOT, get_podcasts_db_path, get_sources_db_path, get_tracking_db_path

DATABASE_OBJECT = "database/podcasts.db"
ARTICLE_DATABASE_OBJECTS = {
    "database/sources.db": get_sources_db_path,
    "database/feed_tracking.db": get_tracking_db_path,
}


def _settings() -> tuple[str, str, str] | None:
    url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
    bucket = os.environ.get("SUPABASE_PODCASTS_BUCKET", "castora-podcasts")
    return (url, key, bucket) if url and key else None


def _required() -> bool:
    default = "true" if (os.environ.get("RENDER") or os.environ.get("RENDER_SERVICE_ID")) else "false"
    return os.environ.get("CLOUD_PODCAST_BACKUP_REQUIRED", default).lower() in {"1", "true", "yes"}


def _article_persistence_enabled() -> bool:
    """Keep the production article store independent from local development."""
    default = "true" if (os.environ.get("RENDER") or os.environ.get("RENDER_SERVICE_ID")) else "false"
    return os.environ.get("PERSIST_ARTICLE_DATABASE", default).lower() in {"1", "true", "yes"}


def _object_url(bucket: str, object_name: str) -> str:
    url, _, _ = _settings()  # caller has already checked configuration
    return f"{url}/storage/v1/object/{quote(bucket, safe='')}/{quote(object_name, safe='/')}"


def _headers() -> dict[str, str]:
    _, key, _ = _settings()  # caller has already checked configuration
    return {"Authorization": f"Bearer {key}", "apikey": key}


def _upload(local_path: Path, object_name: str) -> None:
    _, _, bucket = _settings()
    with local_path.open("rb") as file_handle:
        response = requests.post(
            _object_url(bucket, object_name),
            headers={**_headers(), "x-upsert": "true", "Content-Type": "application/octet-stream"},
            data=file_handle,
            timeout=45,
        )
    response.raise_for_status()


def _download(local_path: Path, object_name: str) -> bool:
    _, _, bucket = _settings()
    response = requests.get(_object_url(bucket, object_name), headers=_headers(), timeout=45)
    if response.status_code == 404:
        return False
    response.raise_for_status()
    local_path.parent.mkdir(parents=True, exist_ok=True)
    local_path.write_bytes(response.content)
    return True


def restore_podcast_backup() -> bool:
    """Restore the saved podcast index before SQLite initializes it."""
    if not _settings():
        if _required():
            print("ERROR: Supabase podcast backup is required but not configured.")
        return False
    try:
        restored = _download(Path(get_podcasts_db_path()), DATABASE_OBJECT)
        if restored:
            print("Restored podcast database from Supabase Storage.")
        return restored
    except Exception as error:
        print(f"WARNING: Could not restore podcast backup: {error}")
        return False


def restore_article_databases() -> int:
    """Restore the source and article store before the API initializes SQLite.

    Render's filesystem is temporary. Without this, every restart creates an
    empty article store and the scheduler only repopulates its first capped
    crawl batch (normally 20 articles).
    """
    if not _article_persistence_enabled():
        return 0
    if not _settings():
        if _required():
            print("ERROR: Supabase backup is required but not configured.")
        return 0
    restored = 0
    for object_name, path_getter in ARTICLE_DATABASE_OBJECTS.items():
        try:
            if _download(Path(path_getter()), object_name):
                restored += 1
                print(f"Restored {object_name} from Supabase Storage.")
        except Exception as error:
            print(f"WARNING: Could not restore {object_name}: {error}")
    return restored


def _checkpoint_database(database_path: Path) -> None:
    """Fold SQLite's WAL into its database before uploading the base file."""
    if not database_path.exists():
        return
    with sqlite3.connect(database_path, timeout=30) as conn:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")


def backup_article_databases() -> tuple[bool, str]:
    """Persist the deployed article/source store after feed ingestion."""
    if not _article_persistence_enabled():
        return True, "Cloud article backup is disabled outside production."
    if not _settings():
        if _required():
            return False, "Supabase backup is required but not configured."
        return True, "Cloud article backup is disabled for local development."
    try:
        uploaded = 0
        for object_name, path_getter in ARTICLE_DATABASE_OBJECTS.items():
            database_path = Path(path_getter())
            if not database_path.exists():
                continue
            _checkpoint_database(database_path)
            _upload(database_path, object_name)
            uploaded += 1
        return True, f"Uploaded {uploaded} article database file(s) to Supabase Storage."
    except Exception as error:
        return False, f"Could not back up article databases: {error}"


def restore_podcast_assets() -> int:
    """Restore approved podcast media referenced by the recovered database."""
    if not _settings() or not Path(get_podcasts_db_path()).exists():
        return 0
    restored = 0
    try:
        with sqlite3.connect(get_podcasts_db_path()) as conn:
            rows = conn.execute("SELECT audio_path, banner_img_path, banner_images FROM podcasts").fetchall()
        filenames = set()
        for audio, banner, banners_json in rows:
            if audio:
                filenames.add(("audio", audio))
            if banner:
                filenames.add(("images", banner))
            try:
                filenames.update(("images", name) for name in json.loads(banners_json or "[]") if name)
            except json.JSONDecodeError:
                pass
        for folder, filename in filenames:
            target = APP_ROOT / "podcasts" / folder / Path(filename).name
            if not target.exists() and _download(target, f"assets/{folder}/{Path(filename).name}"):
                restored += 1
    except Exception as error:
        print(f"WARNING: Could not restore podcast assets: {error}")
    return restored


def backup_approved_podcast(audio_filename: str | None, banner_filename: str | None, banner_images: list[str]) -> tuple[bool, str]:
    """Upload all data needed to show an approved podcast after a redeploy."""
    if not _settings():
        if _required():
            return False, "Supabase backup is not configured; the podcast cannot be approved safely."
        return True, "Cloud backup is disabled for local development."
    try:
        assets = [("audio", audio_filename), ("images", banner_filename)]
        assets.extend(("images", image) for image in banner_images or [])
        for folder, filename in assets:
            if not filename:
                continue
            local_path = APP_ROOT / "podcasts" / folder / Path(filename).name
            if not local_path.exists():
                raise FileNotFoundError(f"Podcast asset is missing: {local_path.name}")
            _upload(local_path, f"assets/{folder}/{local_path.name}")
        # Upload the database last.  It is the manifest that makes an asset
        # visible in the app, so it must never reference a failed upload.
        _upload(Path(get_podcasts_db_path()), DATABASE_OBJECT)
        return True, "Podcast backup uploaded to Supabase Storage."
    except Exception as error:
        return False, f"Could not upload podcast backup: {error}"
