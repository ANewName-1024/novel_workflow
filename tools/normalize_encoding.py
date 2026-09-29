"""tools/normalize_encoding.py — convert all text files to UTF-8 (no BOM).

Run modes:
  python tools/normalize_encoding.py           # dry-run, list what would change
  python tools/normalize_encoding.py --apply   # actually rewrite files

Targets:
  - UTF-16 LE/BE  ->  UTF-8 (no BOM)            (e.g. Notepad-on-Windows accidents)
  - UTF-8 w/ BOM  ->  UTF-8 (no BOM)            (ps2exe / 旧 VS Code 守)

Idempotent: running it again is a no-op once everything is UTF-8 (no BOM).

Pairs with tools/audit_encoding.py. The audit runs every commit (see
``tools/pre-commit-encoding-protection.ps1``) so a regression shows up at commit,
not in production."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
EXCLUDE = (
    ".git", "venv", "__pycache__", "node_modules", "mobile",
    ".venv", "dist", "build", "_internal", "fonts", "data",
    ".gradle",
)
EXTS = (
    ".py", ".html", ".js", ".css", ".json", ".yml", ".yaml",
    ".md", ".txt", ".sh", ".ps1", ".bat", ".cfg", ".ini",
    ".toml", ".rst", ".csv", ".tsv",
)


def decode(raw: bytes) -> tuple[str | None, str | None]:
    """Return (text, current_label) for any decodable file.
    text=None means no change needed (already UTF-8 no-BOM)."""
    if raw.startswith(b"\xef\xbb\xbf"):
        text = raw[3:].decode("utf-8")
        return text, "utf-8 (BOM)"
    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        enc = "utf-16-le" if raw.startswith(b"\xff\xfe") else "utf-16-be"
        text = raw.lstrip(b"\xff\xfe").decode(enc)
        # Strip the leading BOM byte if it survived
        if text.startswith("\ufeff"):
            text = text[1:]
        return text, enc
    try:
        raw.decode("utf-8")
        return None, "utf-8 (already clean)"
    except UnicodeDecodeError:
        pass
    try:
        text = raw.decode("gbk")
        return text, "gbk"
    except UnicodeDecodeError:
        pass
    return None, "binary-or-unsupported"


def iter_files() -> list[Path]:
    out: list[Path] = []
    for p in REPO.rglob("*"):
        if not p.is_file():
            continue
        if p.suffix.lower() not in EXTS:
            continue
        if any(x in str(p) for x in EXCLUDE):
            continue
        out.append(p)
    return sorted(set(out), key=str)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="rewrite files (default: dry-run)")
    args = ap.parse_args()

    files = iter_files()
    changes: list[tuple[Path, str, int]] = []
    for f in files:
        try:
            raw = f.read_bytes()
        except OSError as e:
            print(f"  SKIP  {f.relative_to(REPO)}  (read failed: {e})",
                  file=sys.stderr)
            continue
        if not raw:
            continue
        text, label = decode(raw)
        if text is None:
            continue
        rel = f.relative_to(REPO)
        changes.append((rel, label, len(text.encode("utf-8"))))

    if not changes:
        print("Already clean: every text file is UTF-8 (no BOM).")
        return 0

    print(f"{len(changes)} file(s) to convert "
          f"{'(DRY RUN)' if not args.apply else '(APPLYING)'}:")
    for rel, label, new_size in changes:
        size = (rel.parent / rel.name).stat().st_size
        print(f"  [{label:12s}] {rel}  ({size} -> {new_size} bytes)")

    if not args.apply:
        print()
        print("Re-run with --apply to rewrite these files.")
        return 0

    # Apply: rewrite as UTF-8 no-BOM, atomic-ish via write_text.
    n_ok = 0
    n_fail = 0
    for rel, _, _ in changes:
        path = REPO / rel
        try:
            text, _ = decode(path.read_bytes())
            assert text is not None
            path.write_text(text, encoding="utf-8", newline="")
            n_ok += 1
        except Exception as e:
            print(f"  FAIL  {rel}: {e}", file=sys.stderr)
            n_fail += 1

    print()
    print(f"Converted: {n_ok} ok, {n_fail} failed")
    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())