/* ------------------------------------------------------------------
 * common.js — shared JS helpers for review_ui pages (M2 refactor)
 *
 * Exports (window.NW namespace):
 *   - NW.api(method, path, body)       — fetch wrapper with auto-JSON + error
 *   - NW.toast(msg, type)              — toast notification (ok/err/info)
 *   - NW.confirm(msg, fn)              — styled confirm dialog (Promise)
 *   - NW.escapeHtml(s)                 — XSS-safe escape
 *   - NW.fmtTime(iso)                  — 短时间格式化
 *   - NW.debounce(fn, ms)              — debounce
 *   - NW.spinner(show|hide)            — 全屏 loading 蒙层
 *
 * 每个页面的 <script> 只放业务逻辑, 不要重复写 fetch/alert/toast 代码。
 * ------------------------------------------------------------------ */

(function() {
  "use strict";

  const NW = {};

  // ── API: fetch wrapper ──────────────────────────────────────────────
  /**
   * @param {string} method - GET/POST/PUT/DELETE
   * @param {string} path - URL path (e.g. "/api/book/foo")
   * @param {*} [body] - JSON-serializable body (optional)
   * @returns {Promise<*>} parsed JSON response (or null for empty body)
   * @throws {Error} on !r.ok with status + message
   */
  NW.api = async function(method, path, body = null) {
    const opts = { method, headers: {"Content-Type": "application/json"} };
    if (body !== null) opts.body = JSON.stringify(body);
    const r = await fetch(path, opts);
    const txt = await r.text();
    if (!r.ok) {
      let msg = txt;
      try { msg = JSON.parse(txt).message || JSON.parse(txt).error || msg; } catch {}
      throw new Error(`${r.status}: ${msg}`);
    }
    return txt ? JSON.parse(txt) : null;
  };

  // ── Toast ───────────────────────────────────────────────────────────
  /**
   * @param {string} msg - message text
   * @param {"ok"|"err"|"info"|"warn"} [type="info"]
   * @param {number} [ms=3000] - auto-hide delay (0 = sticky)
   */
  NW.toast = function(msg, type = "info", ms = 3000) {
    let host = document.getElementById("nw-toast-host");
    if (!host) {
      host = document.createElement("div");
      host.id = "nw-toast-host";
      host.className = "toast-host";
      document.body.appendChild(host);
    }
    const el = document.createElement("div");
    el.className = `toast toast-${type}`;
    const icon = {ok: "✅", err: "❌", info: "ℹ️", warn: "⚠️"}[type] || "";
    el.innerHTML = `<span class="toast-icon">${icon}</span><span class="toast-msg"></span>`;
    el.querySelector(".toast-msg").textContent = msg;
    host.appendChild(el);
    // trigger reflow then add .show for animation
    requestAnimationFrame(() => el.classList.add("show"));
    if (ms > 0) {
      setTimeout(() => {
        el.classList.remove("show");
        setTimeout(() => el.remove(), 200);
      }, ms);
    }
    return el;
  };

  // ── Confirm ─────────────────────────────────────────────────────────
  /**
   * @param {string} msg
   * @returns {Promise<boolean>}
   */
  NW.confirm = function(msg) {
    return new Promise((resolve) => {
      const bg = document.createElement("div");
      bg.className = "modal-bg show";
      bg.innerHTML = `
        <div class="modal" style="max-width: 420px;">
          <h2 style="margin: 0 0 12px; font-size: 16px;">⚠️ 确认</h2>
          <div class="confirm-msg" style="font-size: 14px; line-height: 1.6; margin-bottom: 16px; white-space: pre-line;"></div>
          <div style="display: flex; gap: 8px; justify-content: flex-end;">
            <button class="btn cancel" style="min-height:36px;padding:8px 16px;">取消</button>
            <button class="btn primary ok" style="min-height:36px;padding:8px 16px;">确认</button>
          </div>
        </div>
      `;
      bg.querySelector(".confirm-msg").textContent = msg;
      document.body.appendChild(bg);
      const close = (val) => { bg.remove(); resolve(val); };
      bg.querySelector(".cancel").onclick = () => close(false);
      bg.querySelector(".ok").onclick = () => close(true);
      bg.addEventListener("click", (e) => { if (e.target === bg) close(false); });
    });
  };

  // ── Escape HTML ─────────────────────────────────────────────────────
  NW.escapeHtml = function(s) {
    return String(s ?? "").replace(/[&<>"']/g, c => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
    }[c]));
  };

  // ── Time formatting ─────────────────────────────────────────────────
  NW.fmtTime = function(iso) {
    if (!iso) return "";
    try {
      const d = new Date(iso);
      if (isNaN(d.getTime())) return iso;
      const now = new Date();
      const diff = (now - d) / 1000;  // seconds
      if (diff < 60) return "刚刚";
      if (diff < 3600) return `${Math.floor(diff/60)}分钟前`;
      if (diff < 86400) return `${Math.floor(diff/3600)}小时前`;
      const pad = n => String(n).padStart(2, "0");
      return `${d.getMonth()+1}/${d.getDate()} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
    } catch { return iso; }
  };

  // ── Debounce ────────────────────────────────────────────────────────
  NW.debounce = function(fn, ms = 200) {
    let t;
    return function(...args) {
      clearTimeout(t);
      t = setTimeout(() => fn.apply(this, args), ms);
    };
  };

  // ── Spinner (全屏) ──────────────────────────────────────────────────
  let _spinEl = null;
  NW.spinner = function(show) {
    if (show) {
      if (_spinEl) return;
      _spinEl = document.createElement("div");
      _spinEl.className = "nw-spinner-bg";
      _spinEl.innerHTML = '<div class="spinner"></div>';
      document.body.appendChild(_spinEl);
    } else {
      if (_spinEl) { _spinEl.remove(); _spinEl = null; }
    }
  };

  // ── 兼容旧 alert 调用 (渐进迁移, M3 可删) ────────────────────────
  // 把全局 alert() 重定向到 NW.toast, 旧页面立即受益。
  // 等所有页面迁移到 NW.toast 后可以删除。
  window.alert = function(msg) {
    console.warn("[alert→toast]", msg);
    NW.toast(String(msg), "err", 4000);
  };

  // 暴露到 window
  window.NW = NW;
})();