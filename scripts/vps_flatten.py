"""Flatten nested projects/ directory and ensure all expected book dirs exist"""
import os, shutil, sqlite3

projects_dir = "/root/novel_workflow/projects"
nested = os.path.join(projects_dir, "projects")

# Step 1: Flatten nested/projects/* -> projects/*
if os.path.isdir(nested):
    for entry in os.listdir(nested):
        src = os.path.join(nested, entry)
        dst = os.path.join(projects_dir, entry)
        # Remove dst if it exists (e.g. empty dir created by service)
        if os.path.exists(dst):
            if os.path.isdir(dst):
                shutil.rmtree(dst)
            else:
                os.remove(dst)
        shutil.move(src, dst)
        print(f"  moved: {entry}")
    os.rmdir(nested)
    print(f"  removed: {nested}")

# Step 2: Stop service is required BEFORE we restore from db?
# Already done externally. Just verify the structure now.

# Step 3: List final structure
print("\n--- Final projects/ top level ---")
for d in sorted(os.listdir(projects_dir)):
    full = os.path.join(projects_dir, d)
    if os.path.isdir(full):
        n = len(os.listdir(full))
        print(f"  {d}/ ({n} entries)")
    else:
        print(f"  {d}")

# Step 4: Check db consistency
db = os.path.join(projects_dir, ".meta", "db.sqlite3")
if os.path.exists(db):
    con = sqlite3.connect(db)
    rows = con.execute("SELECT id FROM projects").fetchall()
    print(f"\n--- DB projects ({len(rows)}) ---")
    for r in rows:
        bid = r[0]
        full = os.path.join(projects_dir, bid)
        cfg_exists = os.path.exists(os.path.join(full, "config.json"))
        print(f"  {bid}: config.json={cfg_exists}")
