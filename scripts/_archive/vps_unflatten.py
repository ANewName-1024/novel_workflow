"""Fix: zip entry names are like 'projects/X/Y/Z' (single file with that name).
Move each to the correct nested path under projects/."""
import os, shutil, sqlite3

projects_dir = "/root/novel_workflow/projects"

# First, collect ALL entries to move
to_move = []
for entry in os.scandir(projects_dir):
    # Skip .meta, .gitkeep
    if entry.name in (".meta", ".gitkeep", "test_book"):  # test_book is real dir created by service
        if entry.is_dir() and entry.name == "test_book":
            # This is a real empty dir from service; remove if no config.json
            cfg = os.path.join(entry.path, "config.json")
            if not os.path.exists(cfg):
                shutil.rmtree(entry.path)
                print(f"  removed empty real dir: {entry.name}")
        continue
    if entry.is_file() and "\\" in entry.name:
        to_move.append(entry.path)
    elif entry.is_dir():
        # The test_book real dir was already handled
        pass

print(f"Found {len(to_move)} flattened files to move")

# Move each to its nested location
moved = 0
for src in to_move:
    fname = os.path.basename(src)
    # 'projects\\测试书籍\\chapters\\ch_001.md' -> 'projects/测试书籍/chapters/ch_001.md'
    rel = fname.replace("\\", "/")
    dst = os.path.join(projects_dir, rel)
    if os.path.exists(dst):
        # Real nested file already exists; remove flat one
        os.remove(src)
        continue
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.move(src, dst)
    moved += 1
    if moved <= 5:
        print(f"  moved: {fname[:50]}... -> {rel}")

print(f"\nMoved {moved} files")

# Remove now-empty flat-file remnants
print("\n--- Final projects/ structure ---")
def walk_show(d, prefix=""):
    with os.scandir(d) as it:
        for e in sorted(it, key=lambda x: x.name):
            if e.is_dir():
                print(f"{prefix}DIR  {e.name}/")
                walk_show(e.path, prefix + "  ")
            else:
                sz = os.path.getsize(e.path)
                print(f"{prefix}FILE {e.name} ({sz}b)")

walk_show(projects_dir)

# Verify db consistency
db = os.path.join(projects_dir, ".meta", "db.sqlite3")
if os.path.exists(db):
    con = sqlite3.connect(db)
    rows = con.execute("SELECT id FROM projects").fetchall()
    print(f"\n--- DB project check ---")
    for r in rows:
        bid = r[0]
        full = os.path.join(projects_dir, bid)
        cfg = os.path.join(full, "config.json")
        if os.path.exists(cfg):
            print(f"  {bid!r}: OK (config.json present)")
        else:
            print(f"  {bid!r}: MISSING config.json (dir={os.path.isdir(full)})")
