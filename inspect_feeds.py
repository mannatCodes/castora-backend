import os
import sqlite3

os.chdir(os.path.dirname(__file__))

def inspect_sources_db():
    
    db = 'databases/sources.db'
    print('--- sources.db ---')
    print('exists', os.path.exists(db))
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    print('tables', [r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()])
    try:
        print('active sources', cur.execute("SELECT COUNT(*) as c FROM sources WHERE is_active=1").fetchone()['c'])
        print('active source_feeds', cur.execute("SELECT COUNT(*) as c FROM source_feeds WHERE is_active=1").fetchone()['c'])
        print('sample source_feeds')
        for row in cur.execute("SELECT id, source_id, feed_url, feed_type, is_active, last_crawled FROM source_feeds ORDER BY id LIMIT 20").fetchall():
            print(dict(row))
    except Exception as exc:
        print('sources db error', exc)
    conn.close()


def inspect_tracking_db():
    db = 'databases/feed_tracking.db'
    print('--- feed_tracking.db ---')
    print('exists', os.path.exists(db))
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    print('tables', [r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()])
    try:
        print('feed_tracking count', cur.execute("SELECT COUNT(*) as c FROM feed_tracking").fetchone()['c'])
        print('feed_entries by date')
        for row in cur.execute("SELECT date(published_date) as d, COUNT(*) as c FROM feed_entries GROUP BY date(published_date) ORDER BY d DESC LIMIT 20").fetchall():
            print(dict(row))
        print('feed_tracking sample')
        for row in cur.execute("SELECT feed_id, last_processed, last_etag, last_modified, entry_hash FROM feed_tracking ORDER BY feed_id LIMIT 20").fetchall():
            print(dict(row))
    except Exception as exc:
        print('tracking db error', exc)
    conn.close()


if __name__ == '__main__':
    inspect_sources_db()
    inspect_tracking_db()
