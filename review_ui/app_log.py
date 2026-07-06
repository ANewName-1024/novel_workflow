"""
APK 日志接收 + 远程调试端点
===========================
- POST /api/app-log          App 上报日志
- GET  /api/app-log/list     查日志（按 device / level / time 过滤）
- GET  /api/app-log/devices  列出已知设备
- GET  /api/app-log/health   健康检查
- GET  /api/app-log/stats    日志统计

日志格式: JSONL 写入 /root/novel_workflow/logs/app_log.jsonl
"""
from __future__ import annotations
import json
import time
import uuid
import threading
from collections import defaultdict, deque
from pathlib import Path
from flask import Blueprint, jsonify, request, abort

LOG_DIR = Path("/root/novel_workflow/logs")
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "app_log.jsonl"
LOCK = threading.Lock()
MAX_LINES = 5000  # in-memory ring buffer for fast /list

# Thread-safe ring buffer
_buffer: deque = deque(maxlen=MAX_LINES)
_seen_ids: dict = {}  # device_id -> {last_seen, level_counts, msg_count, last_msg}
_last_flush = 0

# Flask Blueprint (用于嵌套在 review_ui app.py 注册)
app_log_bp = Blueprint("app_log", __name__, url_prefix="/api/app-log")


def _safe_json():
    """Parse JSON body, abort 400 on error."""
    try:
        return request.get_json(force=True, silent=False)
    except Exception as e:
        # v1.3 M5: capture raw body for debugging bad client JSON
        try:
            raw = request.get_data(as_text=True)[:500]
            _buffer.append({
                "id": "bad-json-" + str(time.time()),
                "ts": time.time(),
                "level": "error",
                "device_id": "server",
                "device_model": "",
                "app_version": "",
                "msg": f"BAD_JSON: {e}",
                "stack": "",
                "context": {"raw": raw, "content_type": request.headers.get("Content-Type", "")},
            })
        except Exception:
            pass
        abort(400, description=f"Invalid JSON: {e}")


@app_log_bp.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status": "ok",
        "ts": time.time(),
        "buffer_size": len(_buffer),
        "log_file": str(LOG_FILE),
        "log_file_exists": LOG_FILE.exists(),
    })


@app_log_bp.route("/", methods=["POST"])
@app_log_bp.route("", methods=["POST"])
def receive_log():
    """Receive a log entry from the app.

    Body: {
      "device_id": "android-xxx",
      "device_model": "Pixel 7",
      "app_version": "1.0.0+1",
      "level": "info" | "warn" | "error" | "debug",
      "msg": "user message",
      "stack": "optional stack trace",
      "context": {"key": "value"},  # optional
      "ts": 1234567890  # optional, server will use its own if missing
    }
    """
    body = _safe_json()
    if not isinstance(body, dict):
        abort(400, description="Body must be a JSON object")

    level = (body.get("level") or "info").lower()
    if level not in {"debug", "info", "warn", "error", "fatal"}:
        level = "info"

    entry = {
        "id": body.get("id") or str(uuid.uuid4()),
        "ts": body.get("ts") or time.time(),
        "level": level,
        "device_id": body.get("device_id") or "unknown",
        "device_model": body.get("device_model") or "",
        "app_version": body.get("app_version") or "",
        "msg": str(body.get("msg") or "")[:4000],
        "stack": body.get("stack") or "",
        "context": body.get("context") or {},
    }

    with LOCK:
        # Append to ring buffer
        _buffer.append(entry)

        # Update device registry
        did = entry["device_id"]
        if did not in _seen_ids:
            _seen_ids[did] = {
                "first_seen": entry["ts"],
                "last_seen": entry["ts"],
                "msg_count": 0,
                "level_counts": defaultdict(int),
                "last_msg": "",
                "device_model": entry["device_model"],
                "app_version": entry["app_version"],
            }
        d = _seen_ids[did]
        d["last_seen"] = entry["ts"]
        d["msg_count"] += 1
        d["level_counts"][level] += 1
        d["last_msg"] = entry["msg"][:200]
        if entry["device_model"]:
            d["device_model"] = entry["device_model"]
        if entry["app_version"]:
            d["app_version"] = entry["app_version"]

        # Append to JSONL file (line-buffered, safe under concurrent writes)
        try:
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as e:
            return jsonify({"ok": False, "error": f"Write failed: {e}"}), 500

    return jsonify({
        "ok": True,
        "id": entry["id"],
        "ts": entry["ts"],
        "buffer_size": len(_buffer),
    })


@app_log_bp.route("/batch", methods=["POST"])
def receive_batch():
    """Receive multiple log entries at once (used for crash dumps)."""
    body = _safe_json()
    entries = body.get("entries") if isinstance(body, dict) else None
    if not isinstance(entries, list):
        abort(400, description="Body must be {\"entries\": [...]}")
    accepted = 0
    for raw in entries[:200]:  # limit batch size
        if not isinstance(raw, dict):
            continue
        # Reuse single-entry logic
        with LOCK:
            entry = {
                "id": raw.get("id") or str(uuid.uuid4()),
                "ts": raw.get("ts") or time.time(),
                "level": (raw.get("level") or "info").lower(),
                "device_id": raw.get("device_id") or "unknown",
                "device_model": raw.get("device_model") or "",
                "app_version": raw.get("app_version") or "",
                "msg": str(raw.get("msg") or "")[:4000],
                "stack": raw.get("stack") or "",
                "context": raw.get("context") or {},
            }
            if entry["level"] not in {"debug", "info", "warn", "error", "fatal"}:
                entry["level"] = "info"
            _buffer.append(entry)
            try:
                with open(LOG_FILE, "a", encoding="utf-8") as f:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
                accepted += 1
            except Exception:
                pass
    return jsonify({"ok": True, "accepted": accepted, "total": len(entries)})


@app_log_bp.route("/list", methods=["GET"])
def list_logs():
    """List recent logs with optional filters.

    Query params:
      device_id: filter by device
      level: filter by min level (info|warn|error|fatal)
      limit: max entries (default 100, max 1000)
      since_ts: only entries after this timestamp
    """
    device_id = request.args.get("device_id")
    min_level = request.args.get("level", "debug").lower()
    limit = min(int(request.args.get("limit", 100)), 1000)
    since_ts = float(request.args.get("since_ts", 0))

    levels = ["debug", "info", "warn", "error", "fatal"]
    if min_level not in levels:
        min_level = "debug"
    min_idx = levels.index(min_level)

    with LOCK:
        snapshot = list(_buffer)

    filtered = []
    for e in snapshot:
        if device_id and e["device_id"] != device_id:
            continue
        if since_ts and e["ts"] < since_ts:
            continue
        try:
            if levels.index(e["level"]) < min_idx:
                continue
        except ValueError:
            continue
        filtered.append(e)

    # Newest first
    filtered.sort(key=lambda x: x["ts"], reverse=True)
    return jsonify({
        "total": len(filtered),
        "returned": min(limit, len(filtered)),
        "entries": filtered[:limit],
    })


@app_log_bp.route("/devices", methods=["GET"])
def list_devices():
    """List all known devices with their status."""
    with LOCK:
        devices = []
        for did, info in _seen_ids.items():
            d = dict(info)
            d["device_id"] = did
            d["level_counts"] = dict(d["level_counts"])
            devices.append(d)
    devices.sort(key=lambda x: x.get("last_seen", 0), reverse=True)
    return jsonify({"devices": devices, "total": len(devices)})


@app_log_bp.route("/stats", methods=["GET"])
def stats():
    """Log statistics summary."""
    with LOCK:
        level_totals = defaultdict(int)
        device_totals = defaultdict(int)
        for e in _buffer:
            level_totals[e["level"]] += 1
            device_totals[e["device_id"]] += 1

        # Time buckets (last 24h, hourly)
        now = time.time()
        hour_buckets = [0] * 24
        for e in _buffer:
            age_h = (now - e["ts"]) / 3600
            if 0 <= age_h < 24:
                hour_buckets[int(age_h)] += 1

    return jsonify({
        "total_entries": len(_buffer),
        "by_level": dict(level_totals),
        "by_device_top10": dict(
            sorted(device_totals.items(), key=lambda x: -x[1])[:10]
        ),
        "last_24h_per_hour": hour_buckets,
        "log_file_size": LOG_FILE.stat().st_size if LOG_FILE.exists() else 0,
        "log_file_lines": sum(1 for _ in open(LOG_FILE, encoding="utf-8")) if LOG_FILE.exists() else 0,
    })


@app_log_bp.route("/clear", methods=["POST"])
def clear_logs():
    """Clear all logs (admin only — should add auth later)."""
    with LOCK:
        _buffer.clear()
        _seen_ids.clear()
        try:
            LOG_FILE.unlink()
        except FileNotFoundError:
            pass
    return jsonify({"ok": True, "cleared": True})
