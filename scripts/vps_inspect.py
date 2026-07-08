"""Use os.scandir to see REAL filenames (PowerShell ssh wrapper mangles display)"""
import os, sqlite3

projects_dir = "/root/novel_workflow/projects"
print("--- Real entries in projects/ (using os.scandir) ---")
with os.scandir(projects_dir) as it:
    for entry in it:
        kind = "DIR " if entry.is_dir() else "FILE"
        print(f"  {kind} {entry.name!r}")

# Check if 'projects' (the nested dir) actually exists
nested = os.path.join(projects_dir, "projects")
print(f"\n  nested exists? {os.path.isdir(nested)}")
if os.path.isdir(nested):
    print(f"  nested contents:")
    for entry in os.scandir(nested):
        kind = "DIR " if entry.is_dir() else "FILE"
        print(f"    {kind} {entry.name!r}")

# Check db
db = os.path.join(projects_dir, ".meta", "db.sqlite3")
con = sqlite3.connect(db)
rows = con.execute("SELECT id, name FROM projects").fetchall()
print(f"\n--- DB projects ---")
for r in rows:
    print(f"  id={r[0]!r}  name={r[1]!r}")
    bid = r[0]
    full = os.path.join(projects_dir, bid)
    print(f"    fs dir exists: {os.path.isdir(full)}")
    if os.path.isdir(full):
        cfg = os.path.join(full, "config.json")
        print(f"    config.json exists: {os.path.exists(cfg)}")
