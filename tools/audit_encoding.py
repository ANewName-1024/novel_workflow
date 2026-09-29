"""tools/audit_encoding.py — stdlib encoding audit.

Run: python tools/audit_encoding.py

Strategy: try-utf-8-strict first (most files). If it fails, try gbk. Anything
else is suspicious. This is more accurate than chardet for our corpus and
adds zero dependencies."""
from __future__ import annotations

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
SAMPLE_LIMIT = 12


def classify(raw: bytes) -> str:
    """Decode raw bytes; return a label.

    Labels:
      'utf-8 (ASCII-only)'    : pure ASCII, subset of UTF-8
      'utf-8 (no BOM)'        : valid UTF-8 with non-ASCII content
      'utf-8 (BOM)'           : starts with EF BB BF
      'utf-16-le' / 'utf-16-be'
      'gbk'                  : GBK-decodable but not valid UTF-8
      'binary'                : neither utf-8 nor gbk decodable
    """
    if raw.startswith(b"\xef\xbb\xbf"):
        try:
            raw.decode("utf-8")
            return "utf-8 (BOM)"
        except UnicodeDecodeError:
            pass
    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        try:
            raw.decode("utf-16")
            return "utf-16"
        except UnicodeDecodeError:
            pass
    try:
        raw.decode("utf-8")
        # is ASCII-only?
        nonascii = any(b >= 128 for b in raw)
        return "utf-8 (ASCII-only)" if not nonascii else "utf-8 (no BOM)"
    except UnicodeDecodeError:
        pass
    try:
        raw.decode("gbk")
        return "gbk"
    except UnicodeDecodeError:
        pass
    return "binary"


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
    files = iter_files()
    buckets: dict[str, list[Path]] = {}
    total_bytes = 0
    for f in files:
        try:
            raw = f.read_bytes()
        except OSError:
            continue
        if not raw:
            continue
        total_bytes += len(raw)
        label = classify(raw)
        buckets.setdefault(label, []).append(f)

    print(f"Total files scanned : {len(files)}")
    print(f"Total bytes         : {total_bytes:,}")
    print()
    print("Breakdown:")
    for k in sorted(buckets, key=lambda k: -len(buckets[k])):
        print(f"  {k:25s}  {len(buckets[k]):5d}")

    def show(label: str, banner: str) -> None:
        paths = buckets.get(label, [])
        print(f"--- {banner} (count={len(paths)}) ---")
        if not paths:
            print("  (none)")
            return
        for p in paths[:SAMPLE_LIMIT]:
            print(f"  {p.relative_to(REPO)}")
        if len(paths) > SAMPLE_LIMIT:
            print(f"  ... and {len(paths) - SAMPLE_LIMIT} more")

    print()
    show("utf-8 (BOM)", "UTF-8 with BOM (Windows-ism; usually unwanted)")
    show("gbk",         "GBK (non-UTF-8, needs conversion)")
    show("utf-16",      "UTF-16 (Windows notepad accidental)")
    show("binary",      "Binary (not a text file?)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())