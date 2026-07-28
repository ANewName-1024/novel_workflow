"""Deep flatten: walk through ALL nested projects/projects/... and move to correct location"""
import os, shutil, sqlite3

ROOT = "/root/novel_workflow"
projects_dir = os.path.join(ROOT, "projects")

# Walk the entire tree, collect all files with their relative path
# and move them to the correct position (one level up from where they are now)

# Step 1: walk and find files that are nested too deep
# Currently: projects/projects/<book>/...  (need to be: projects/<book>/...)
# But also: projects/projects/test_book/chapters/ projects\test_book\chapters\ (zero-byte fake)

def collect_all_files(root):
    """Yield (abs_path, rel_path_from_root) for every regular file under root."""
    for dirpath, dirnames, filenames in os.walk(root):
        for fn in filenames:
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, root)
            yield full, rel

# Step 2: For each file, determine where it should be
# Strategy: relpath like 'projects/test_book/config.json' should become 'test_book/config.json'
def canonical_rel(rel):
    """Strip leading 'projects/' or 'projects\\' from a relative path."""
    parts = rel.replace("\\", "/").split("/")
    # Remove leading 'projects'
    if parts and parts[0] == "projects":
        parts = parts[1:]
    return "/".join(parts)

# Step 3: Walk and move
moves = []
deletes = []  # zero-byte files with backslash in name (fake flat files)
for src, rel in collect_all_files(projects_dir):
    canon = canonical_rel(rel)
    dst = os.path.join(projects_dir, canon)
    # Skip if already in correct place
    if os.path.normpath(src) == os.path.normpath(dst):
        continue
    # If file name is e.g. 'projects\\test_book\\chapters\\' (zero-byte fake), delete it
    if "\\" in os.path.basename(src) and os.path.getsize(src) == 0:
        deletes.append(src)
    else:
        moves.append((src, dst))

print(f"Moves planned: {len(moves)}")
print(f"Fake files to delete: {len(deletes)}")

# Execute moves
for src, dst in moves:
    if os.path.exists(dst):
        # target already exists (e.g. real nested from manual mkdir)
        # Keep the larger one
        if os.path.getsize(src) > os.path.getsize(dst):
            os.remove(dst)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.move(src, dst)
        else:
            os.remove(src)
    else:
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.move(src, dst)

# Delete fake zero-byte files
for fake in deletes:
    try:
        os.remove(fake)
    except FileNotFoundError:
        pass

# Remove any empty directories left behind
for dirpath, dirnames, filenames in os.walk(projects_dir, topdown=False):
    if not os.listdir(dirpath) and dirpath not in (projects_dir, os.path.join(projects_dir, ".meta")):
        try:
            os.rmdir(dirpath)
            print(f"  removed empty dir: {dirpath}")
        except OSError:
            pass

# Final structure
print("\n--- Final structure (depth 2) ---")
for entry in sorted(os.scandir(projects_dir), key=lambda e: e.name):
    kind = "DIR " if entry.is_dir() else "FILE"
    print(f"  {kind} {entry.name!r}")
    if entry.is_dir():
        for sub in sorted(os.scandir(entry.path), key=lambda e: e.name):
            kind = "DIR " if sub.is_dir() else "FILE"
            print(f"    {kind} {sub.name!r}")

# DB check
db = os.path.join(projects_dir, ".meta", "db.sqlite3")
con = sqlite3.connect(db)
rows = con.execute("SELECT id FROM projects").fetchall()
print(f"\n--- DB project check ---")
for r in rows:
    bid = r[0]
    full = os.path.join(projects_dir, bid)
    cfg = os.path.join(full, "config.json")
    print(f"  {bid!r}: dir={os.path.isdir(full)}  config.json={os.path.exists(cfg)}")
