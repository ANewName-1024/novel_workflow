import zipfile
import os
import shutil

src = "/tmp/novel_projects.zip"
dst = "/root/novel_workflow/projects"

# Clear old book dirs but keep .meta (sqlite db) - we'll overwrite later if needed
for d in os.listdir(dst):
    full = os.path.join(dst, d)
    if os.path.isdir(full) and d != ".meta":
        shutil.rmtree(full)
        print(f"  removed: {d}")

with zipfile.ZipFile(src) as z:
    z.extractall(dst)
    print(f"  extracted {len(z.namelist())} files")

print("\n--- Final projects/ ---")
for d in sorted(os.listdir(dst)):
    print(f"  {d}")

print("\n--- 测试书籍 contents ---")
test_book = os.path.join(dst, "测试书籍")
if os.path.isdir(test_book):
    for f in sorted(os.listdir(test_book)):
        print(f"  {f}")
