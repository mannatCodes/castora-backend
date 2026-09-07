import os
import sqlite3

os.chdir(os.path.dirname(__file__))

db = 'databases/tasks.db'
print('--- tasks.db ---')
print('exists', os.path.exists(db))
conn = sqlite3.connect(db)
conn.row_factory = sqlite3.Row
cur = conn.cursor()
print('tables', [r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()])
try:
    print('tasks count', cur.execute("SELECT COUNT(*) as c FROM tasks").fetchone()['c'])
    for row in cur.execute("SELECT id, name, command, frequency, frequency_unit, enabled, last_run, created_at FROM tasks ORDER BY id").fetchall():
        print(dict(row))
except Exception as exc:
    print('tasks db error', exc)
try:
    print('task_executions count', cur.execute("SELECT COUNT(*) as c FROM task_executions").fetchone()['c'])
    for row in cur.execute("SELECT id, task_id, start_time, end_time, status, error_message FROM task_executions ORDER BY start_time DESC LIMIT 20").fetchall():
        print(dict(row))
except Exception as exc:
    print('executions db error', exc)
conn.close()
