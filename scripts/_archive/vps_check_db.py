"""Inspect SQLite db on VPS"""
import sqlite3, os

db = "/root/novel_workflow/projects/.meta/db.sqlite3"
print(f"DB size: {os.path.getsize(db)} bytes")
print(f"WAL: {os.path.getsize(db + '-wal') if os.path.exists(db + '-wal') else 'none'}")
print(f"SHM: {os.path.getsize(db + '-shm') if os.path.exists(db + '-shm') else 'none'}")

con = sqlite3.connect(db)
cur = con.cursor()
cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = [r[0] for r in cur.fetchall()]
print(f"\nTables: {tables}")

for t in tables:
    try:
        cur.execute(f"SELECT COUNT(*) FROM {t}")
        n = cur.fetchone()[0]
        print(f"  {t}: {n} rows")
    except Exception as e:
        print(f"  {t}: err {e}")

if "projects" in tables:
    cur.execute("SELECT name, display_name FROM projects")
    print(f"\nProjects in DB:")
    for r in cur.fetchall():
        print(f"  {r}")
