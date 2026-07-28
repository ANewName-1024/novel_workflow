"""Fix: zip had a projects/ prefix - move the nested projects/ up one level"""
import os, shutil

projects_dir = "/root/novel_workflow/projects"
nested = os.path.join(projects_dir, "projects")

if os.path.isdir(nested):
    # Move contents of nested/projects/ to projects/
    for entry in os.listdir(nested):
        src = os.path.join(nested, entry)
        dst = os.path.join(projects_dir, entry)
        if os.path.exists(dst):
            if os.path.isdir(dst):
                shutil.rmtree(dst)
            else:
                os.remove(dst)
        shutil.move(src, dst)
        print(f"  moved: {entry}")
    # Remove the now-empty projects/ subdir
    os.rmdir(nested)
    print("  removed empty projects/projects/")

print("\n--- Final projects/ ---")
for d in sorted(os.listdir(projects_dir)):
    print(f"  {d}")
