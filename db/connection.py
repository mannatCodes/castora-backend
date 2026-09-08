import sqlite3
from contextlib import contextmanager


@contextmanager
def db_connection(db_path):
    # The API, scheduler and Celery worker share the persistent SQLite files
    # in production. Wait for a short-lived writer rather than failing reads
    # intermittently with "database is locked".
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA journal_mode = WAL")
    try:
        yield conn
    finally:
        conn.close()


def execute_query(db_path, query, params=(), fetch=False, fetch_one=False):
    with db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(query, params)

        if fetch_one:
            result = cursor.fetchone()
            return dict(result) if result else None
        elif fetch:
            return [dict(row) for row in cursor.fetchall()]
        else:
            conn.commit()
            return cursor.lastrowid
