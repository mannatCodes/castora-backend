import sqlite3


def test_article_database_backup_and_restore_use_the_same_cloud_objects(monkeypatch, tmp_path):
    from services import podcast_backup_service as backup

    sources_path = tmp_path / "sources.db"
    tracking_path = tmp_path / "feed_tracking.db"
    for database_path in (sources_path, tracking_path):
        with sqlite3.connect(database_path) as conn:
            conn.execute("CREATE TABLE marker (value TEXT)")
            conn.execute("INSERT INTO marker VALUES ('ready')")

    objects = {
        "database/sources.db": lambda: str(sources_path),
        "database/feed_tracking.db": lambda: str(tracking_path),
    }
    uploaded = []
    restored = []

    monkeypatch.setattr(backup, "ARTICLE_DATABASE_OBJECTS", objects)
    monkeypatch.setattr(backup, "_settings", lambda: ("https://example.test", "key", "bucket"))
    monkeypatch.setattr(backup, "_upload", lambda path, object_name: uploaded.append((path, object_name)))
    monkeypatch.setattr(
        backup,
        "_download",
        lambda path, object_name: restored.append((path, object_name)) or True,
    )

    success, _ = backup.backup_article_databases()

    assert success is True
    assert [object_name for _, object_name in uploaded] == list(objects)
    assert backup.restore_article_databases() == 2
    assert [object_name for _, object_name in restored] == list(objects)

