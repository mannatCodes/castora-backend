import sqlite3

conn = sqlite3.connect('databases/feed_tracking.db')
ai_status = conn.execute("SELECT ai_status, COUNT(*) FROM crawled_articles GROUP BY ai_status").fetchall()
print(f'AI Status breakdown: {ai_status}')
conn.close()