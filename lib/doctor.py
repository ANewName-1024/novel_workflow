"""
doctor.py — 环境诊断 (novel doctor)

检测:
- Python 版本 ≥ 3.12
- llama-server 可达 (默认 :60443/v1/models 返回 200)
- 依赖包都装了 (openai / flask / pytest ...)
- 项目目录可写
- 磁盘空间 ≥ 1GB
- 端口 21199 没被占 (review_ui 还没跑)
- **配置漂移** (见下面「配置漂移检测」一节, 可选, 默认不跑)

输出: 9 个 ✅/⚠/❌ + 修复建议
"""
from __future__ import annotations

import os
import re
import sys
import shutil
import socket
import time
import logging
import platform
from pathlib import Path
from typing import Any, NamedTuple, Optional

from .config_loader import get_config

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent

MIN_PY = (3, 12)
MIN_DISK_GB = 1.0


class CheckResult(NamedTuple):
    name: str
    status: str  # "ok" | "warn" | "fail"
    detail: str


def check_python() -> CheckResult:
    v = sys.version_info
    if v >= MIN_PY:
        return CheckResult("Python 版本", "ok", f"{v.major}.{v.minor}.{v.micro} (≥ {MIN_PY[0]}.{MIN_PY[1]})")
    return CheckResult("Python 版本", "fail", f"{v.major}.{v.minor}.{v.micro} < {MIN_PY[0]}.{MIN_PY[1]}")


def check_deps() -> CheckResult:
    missing = []
    for mod in ("openai", "flask", "yaml"):
        try:
            __import__(mod)
        except ImportError:
            missing.append(mod)
    if not missing:
        return CheckResult("依赖", "ok", "openai + flask + yaml 都在")
    return CheckResult("依赖", "fail", f"缺: {', '.join(missing)} (pip install -r requirements.txt)")


def check_llm(cfg: dict) -> CheckResult:
    """探测 llama-server /v1/models."""
    try:
        import urllib.request
        import urllib.error
        api_base = cfg.get("llm", {}).get("api_base", "http://127.0.0.1:60443/v1")
        # api_base 通常 .../v1, 改 .../models
        url = api_base.rstrip("/")
        if not url.endswith("/models"):
            if url.endswith("/v1"):
                url += "/models"
            else:
                url += "/models"
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=3) as resp:
            body = resp.read().decode("utf-8", errors="ignore")[:200]
            return CheckResult("LLM (llama-server)", "ok", f"{url} → 200 ({len(body)} bytes)")
    except Exception as e:
        return CheckResult("LLM (llama-server)", "fail",
                           f"{cfg.get('llm', {}).get('api_base', 'http://127.0.0.1:60443/v1')} 不可达: {type(e).__name__}: {e}")


def check_disk() -> CheckResult:
    try:
        total, used, free = shutil.disk_usage(ROOT)
        free_gb = free / (1024 ** 3)
        if free_gb >= MIN_DISK_GB:
            return CheckResult("磁盘空间", "ok", f"{free_gb:.1f} GB ≥ {MIN_DISK_GB} GB")
        return CheckResult("磁盘空间", "fail", f"{free_gb:.1f} GB < {MIN_DISK_GB} GB")
    except Exception as e:
        return CheckResult("磁盘空间", "warn", f"无法检测: {e}")


def check_port_free(cfg: dict) -> CheckResult:
    port = cfg.get("review_ui", {}).get("port", 21199)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
            return CheckResult(f"端口 {port}", "ok", "空闲")
        except OSError:
            return CheckResult(f"端口 {port}", "warn", "已被占用 (review_ui 可能在跑)")


def check_projects_dir(cfg: dict) -> CheckResult:
    root_rel = cfg.get("projects", {}).get("root", "projects")
    p = ROOT / root_rel
    if not p.exists():
        return CheckResult("项目目录", "warn", f"{p} 不存在, 但会自动创建")
    if not os.access(str(p), os.W_OK):
        return CheckResult("项目目录", "fail", f"{p} 不可写")
    return CheckResult("项目目录", "ok", str(p))


def check_git() -> CheckResult:
    git_dir = ROOT / ".git"
    if git_dir.exists():
        return CheckResult("Git", "ok", "已 init")
    return CheckResult("Git", "warn", "未初始化 (可选, 推荐)")


def check_paths() -> CheckResult:
    critical = ["novel.py", "lib/__init__.py", "lib/llm.py", "lib/storage.py",
                "lib/outline.py", "lib/chapter.py", "lib/review_service.py",
                "review_ui/app.py"]
    missing = [p for p in critical if not (ROOT / p).exists()]
    if not missing:
        return CheckResult("关键文件", "ok", f"{len(critical)} 个都在")
    return CheckResult("关键文件", "fail", f"缺: {', '.join(missing)}")


def run_all() -> list[CheckResult]:
    cfg = get_config()
    return [
        check_python(),
        check_paths(),
        check_deps(),
        check_git(),
        check_llm(cfg),
        check_disk(),
        check_port_free(cfg),
        check_projects_dir(cfg),
    ]


# ── 配置漂移检测 (2026-10-02) ───────────────────────────────────────────────
#
# 为什么要有这一节: 生产实跑发现的两类故障, 上面 8 项检查**全都测不出来**。
#
#   1) 模型名与账号能力不符。
#      test_book 配 llm_provider=deepseek + llm_model=deepseek-chat, 而那把
#      key 的账号只接受 deepseek-flash / deepseek-v4-pro。传错名字 → HTTP
#      400: "The supported API model names are deepseek-flash, deepseek-v4-pro,
#      but you passed deepseek-chat."
#      而 lib/llm_providers.resolve_model() 对不在列表里的模型是**照发只警
#      告**(设计如此: 允许自建模型名), 于是书看着配好了, 一写就 400, doctor 全绿。
#
#   2) 端点指向死服务。
#      init 把新书默认绑到 http://127.0.0.1:60443/v1, 而那台机器上没有进程
#      在监听。check_llm() 只按 config.yaml 里的全局 llm.api_base 探一次,
#      压根不看「这本书实际会用哪个端点」—— per-book 覆盖(以及 provider 注册
#      表里的地址)都不在它的视野里。
#
# 所以这里**按书**解析一遍实际生效的 LLM 配置(resolve_for_book), 再对那个
# 端点做一次最小探测。
#
# 两条铁律:
#   - **只读**。绝不改用户的 book config。发现问题只报告, 修不修由人决定 ——
#     doctor 的定位是诊断, 一旦它开始写配置, "doctor 跑完书就变了" 就成了
#     比原故障更难查的问题。
#   - **必须有短超时 + 整轮预算**。串行探测 N 本书最坏是 N × 超时, 书多了
#     doctor 会从"诊断工具"变成"卡住的东西"。所以: 同一 (端点, 模型) 只探
#     一次(多本书共用配置是常态), 并且整轮有一个总预算, 预算用尽就如实报
#     "没探到", 而不是继续等。

DRIFT_PING_TIMEOUT_SEC = 15     # 单次 LLM.ping() 的超时上限(ping 自己的默认值)
DRIFT_TOTAL_BUDGET_SEC = 60.0   # 整轮探测预算: 超过就不再发新请求

# 400 报文里"这把 key 能用哪些模型"的固定句式。逐字照抄上游的措辞是因为
# 这是**唯一**能证明"注册表过期了"的信号: 光看 models 列表只能说明我们
# 不知道, 400 能说明我们确实错了, 而且答案就在报文里。
#
# 名字之间是**逗号分隔**的, 所以终止符不能写成"遇到逗号为止" —— 那只会抠出
# 第一个名字。真正可靠的终止标志是报文后半句 ", but you passed <X>"; 拿不到
# 它时退回到 句号 / 引号 / 换行 / 分号 / 行尾。
_SUPPORTED_MODELS_RE = re.compile(
    r"supported API model names are\s+(?P<names>[^\n\";]+?)"
    r"(?:\s*,?\s*but you passed|\.|\"|;|$)",
    re.IGNORECASE | re.MULTILINE)

# kind → 中文名, 给 CLI 渲染用。
DRIFT_KINDS = (
    "model_unknown",       # 模型名不在 provider 的已知列表
    "model_rejected",      # API 400 明确说这个模型名不收(最有价值的一条)
    "endpoint_unreachable",  # 连不上 / 超时
    "api_key",             # 401 / 403
    "endpoint_bad_request",  # 400, 但报文里没有可用模型清单
    "endpoint_not_found",  # 404
    "token_budget",        # 推理模型的 max_tokens 下限(信息项)
    "endpoint_overridden",  # book 覆盖了注册表里的 api_base(信息项)
    "probe_skipped",       # 预算用尽, 没探到
    "resolve_error",       # 这本书的配置根本解析不出来
)

_SEVERITY_STATUS = {"fatal": "fail", "warn": "warn", "info": "ok"}


class DriftFinding(NamedTuple):
    """一条漂移。severity 决定它是"写不出东西"还是"能用但有隐患"。"""
    book: str
    kind: str
    severity: str            # "fatal" | "warn" | "info"
    detail: str
    hint: str = ""           # 下一步该干什么(不改配置, 只给建议)
    data: Optional[dict] = None

    @property
    def status(self) -> str:
        return _SEVERITY_STATUS.get(self.severity, "warn")

    def as_dict(self) -> dict[str, Any]:
        d = self._asdict()
        d["status"] = self.status
        return d


class DriftReport(NamedTuple):
    """整轮检测的结论。findings 逐条, by_kind 给每类一个独立状态。"""
    books: int               # 看了几本书
    findings: list[DriftFinding]
    by_kind: dict[str, str]  # kind → "ok" | "warn" | "fail"(没命中就是 ok)
    probes: int              # 实际发出去几次探测(去重后)
    probed: bool             # 本轮是否允许发网络请求
    elapsed_ms: int
    time_note: str           # 耗时估算/预算说明, 直接给 CLI 打
    ok: bool                 # 没有 fatal → 流水线能写

    @property
    def fatal(self) -> list[DriftFinding]:
        return [f for f in self.findings if f.severity == "fatal"]

    @property
    def warns(self) -> list[DriftFinding]:
        return [f for f in self.findings if f.severity == "warn"]

    @property
    def infos(self) -> list[DriftFinding]:
        return [f for f in self.findings if f.severity == "info"]

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "books": self.books,
            "probes": self.probes,
            "probed": self.probed,
            "elapsed_ms": self.elapsed_ms,
            "time_note": self.time_note,
            "by_kind": dict(self.by_kind),
            "findings": [f.as_dict() for f in self.findings],
        }


def _error_text(exc: BaseException) -> str:
    """把异常的报文凑齐。

    openai SDK 的 str(e) 只带一句摘要(比如 "Error code: 400"), 真正有用的
    那句 "The supported API model names are ..." 在 response body 里。两个
    都留着再一起做正则匹配 —— 摘要已经能说明"出错了", 但只有 body 能告诉
    用户"该换成哪个模型名"。
    """
    parts: list[str] = []
    text = getattr(getattr(exc, "response", None), "text", None)
    if isinstance(text, str) and text.strip():
        parts.append(text)
    summary = str(exc)
    if summary.strip():
        parts.append(summary)
    return "\n".join(parts)


def _extract_supported_models(exc: BaseException) -> list[str]:
    """从 400 报文里抠出"这把 key 实际接受的模型名"。

    抠不出来就返回空列表(而不是猜): 这条提示的价值全在于它是 API 亲口说的,
    猜出来的名字不能写进报告, 否则用户会拿一个我们自己编的名字去改配置。
    """
    m = _SUPPORTED_MODELS_RE.search(_error_text(exc))
    if not m:
        return []
    names = [n.strip().strip("'`\".,") for n in m.group("names").split(",")]
    return [n for n in names if n]


def _port_of(api_base: str) -> Optional[int]:
    """从 api_base 里取出端口, 用来在报错时说清"是不是没人监听这个端口"。"""
    m = re.search(r":(\d{2,5})", api_base or "")
    return int(m.group(1)) if m else None


def _classify_ping_error(exc: BaseException, provider: str) -> DriftFinding:
    """把一次失败的探测归类。

    必须区分「网络不通」和「模型名不对」: 这两者的修法完全相反 ——
    前者去把服务拉起来(或者改端点), 后者去改书里的模型名。混成一句
    "连接失败" 的话, 用户八成会去查防火墙, 而真正坏的是一行模型名。
    """
    status = getattr(exc, "status_code", None)
    provider_name = provider or "LLM"
    env_key = f"{provider_name.upper()}_API_KEY"

    if status is None:
        # 没有状态码 = 根本没到 HTTP 层。APIConnectionError / APITimeoutError
        # 以及 socket 层的 ConnectionRefusedError / TimeoutError(它们都是
        # OSError 的子类) 都归到这里。
        return DriftFinding(
            book="", kind="endpoint_unreachable", severity="fatal",
            detail=f"连不上 ({type(exc).__name__}: {exc})",
            hint="确认该端口是否有服务在监听(本地模型要先起 llama-server; "
                 "云端确认机器能出网 / 代理是否放行)",
        )

    if status == 400:
        models = _extract_supported_models(exc)
        if models:
            # 最有价值的一条: API 亲口说了这把 key 能用哪些名字。直接把它们
            # 原样报给用户, 别让他自己去翻 400 日志。
            return DriftFinding(
                book="", kind="model_rejected", severity="fatal",
                detail=f"HTTP 400 — 端点拒绝该模型名; 可用模型: {', '.join(models)}",
                hint="把书配置里的 llm_model 改成上面列出的名字之一",
                data={"supported_models": models},
            )
        return DriftFinding(
            book="", kind="endpoint_bad_request", severity="warn",
            detail=f"HTTP 400 — {_error_text(exc)[:200]}",
            hint="报文里没有可用模型清单, 看完整请求日志定位",
        )

    if status in (401, 403):
        return DriftFinding(
            book="", kind="api_key", severity="fatal",
            detail=f"HTTP {status} — key 被拒 ({type(exc).__name__})",
            hint=f"去 .env 配 {env_key}, 并确认它与当前 provider({provider_name})匹配",
            data={"env_key": env_key},
        )

    if status == 404:
        return DriftFinding(
            book="", kind="endpoint_not_found", severity="warn",
            detail="HTTP 404 — 端点存在但没有这个 chat/completions 路径",
            hint="检查 api_base 是否该以 /v1 结尾",
        )

    return DriftFinding(
        book="", kind="endpoint_bad_request", severity="warn",
        detail=f"HTTP {status} — {type(exc).__name__}: {str(exc)[:160]}",
        hint="看端点日志",
    )


def _probe_one(resolved: dict[str, Any], timeout_sec: float) -> Optional[DriftFinding]:
    """对一份已解析的 LLM 配置做一次最小连通性探测。

    **必须**走 lib.llm.LLM.ping(): 它绕开 min_max_tokens 下限和空响应升级
    重试, 只问"有没有人应答"。自己再写一套 HTTP 的话, 对推理模型
    (min_max_tokens=16384) 一次只想确认"通不通"的探测会变成 16384 → 65536
    token 的又慢又贵的请求 —— 那正是 ping() 存在的理由。

    返回 None = 通; 返回 DriftFinding = 出了问题(调用方补上 book 名)。
    """
    from . import llm as llm_mod

    api_base = resolved.get("api_base") or ""
    provider = resolved.get("provider") or ""
    try:
        client = llm_mod.LLM(
            model=resolved.get("model"),
            api_base=api_base,
            api_key=resolved.get("api_key"),
            min_max_tokens=int(resolved.get("min_max_tokens") or 0),
        )
    except Exception as e:
        return DriftFinding(
            book="", kind="resolve_error", severity="warn",
            detail=f"构造客户端失败 ({type(e).__name__}: {e})",
        )
    try:
        client.ping(timeout_sec=timeout_sec)
        return None
    except Exception as e:
        f = _classify_ping_error(e, provider)
        # 端点不通时把端口点出来: "连不上"和"没人监听"对用户是两件事,
        # 而 ConnectionRefusedError 的原文通常只有一个 [WinError 10061]。
        if f.kind == "endpoint_unreachable" and _port_of(api_base):
            f = f._replace(detail=f"{f.detail} (端口 {_port_of(api_base)})")
        return f


def check_llm_config_drift(
    probe: bool = True,
    timeout_sec: int = DRIFT_PING_TIMEOUT_SEC,
    budget_sec: float = DRIFT_TOTAL_BUDGET_SEC,
    books: Optional[list[str]] = None,
    max_books: Optional[int] = None,
) -> DriftReport:
    """逐书检查 LLM 配置有没有和"实际能用的东西"对不上。

    只读。发现问题只报告, **绝不改**任何 book config。

    参数:
        probe:      是否发真实网络请求。False = 只做本地静态检查(模型名是否在
                    注册表里 / token 预算 / 端点覆盖), 零请求零费用。
        timeout_sec: 单次探测超时上限。
        budget_sec: 整轮探测预算; 用尽后剩下的书只报"没探到", 不再发请求。
        books:      指定要查的书(测试用); None = 自动列 projects/ 下所有书。
        max_books:  最多查几本(按名字排序取前 N)。

    返回 DriftReport, fields 见定义。致命(fatal)= 直接写不出东西;
    告警(warn)= 能用但有隐患; 信息(info)= 只是让人知道当前绑定的是什么。
    """
    t0 = time.perf_counter()
    findings: list[DriftFinding] = []

    if books is None:
        from . import storage
        try:
            books = list(storage.list_projects())
        except Exception as e:      # 列不出来就当没有书, 绝不让 doctor 挂掉
            log.warning("列项目失败, 跳过漂移检测: %s", e)
            books = []
    if max_books:
        books = books[:max_books]

    # 探测结果按 (provider, 端点, 模型) 缓存。多本书共用同一套配置是常态
    # (比如 20 本书全走同一个 deepseek), 逐本探就是同一个请求发 20 遍 ——
    # 既慢又平白多花钱。命中缓存只省请求, 报告里仍然逐本书各报一条。
    probe_cache: dict[tuple, Optional[DriftFinding]] = {}
    deadline = t0 + max(0.0, float(budget_sec))
    probes = 0
    planned = 0

    for book in books:
        from . import llm_providers
        try:
            resolved = llm_providers.resolve_for_book(book)
        except Exception as e:
            # 典型来源: 书里写了注册表里没有的 llm_provider, resolve_model 抛 KeyError。
            findings.append(DriftFinding(
                book=book, kind="resolve_error", severity="fatal",
                detail=f"解析 LLM 配置失败 ({type(e).__name__}: {e})",
                hint="检查书配置里的 llm_provider 是否是已注册的 provider 名",
            ))
            continue

        provider = resolved.get("provider") or "?"
        model = resolved.get("model") or "?"
        api_base = resolved.get("api_base") or ""
        min_max_tokens = int(resolved.get("min_max_tokens") or 0)

        # 1) 模型名 vs provider 的已知列表
        try:
            known: list[str] = list(
                llm_providers.get_provider_config(provider).get("models", []))
        except KeyError:
            known = []
        if known and model not in known:
            # 只告警, 不判死: 注册表是人工维护的, 可能过期。反过来, 下面
            # 探测真拿到 400 才说明"确实错了" —— 那时才升级成 fatal。
            hint = f"改成 {', '.join(known)} 之一(或在 config.yaml 里补进该 provider 的 models)"
            if min_max_tokens:
                hint += f"; 注意它是推理模型, max_tokens 下限 = {min_max_tokens}"
            findings.append(DriftFinding(
                book=book, kind="model_unknown", severity="warn",
                detail=f"provider={provider} 的已知模型里没有 {model!r} "
                       f"(已知: {', '.join(known)})",
                hint=hint,
                data={"provider": provider, "model": model, "candidates": known},
            ))

        # 2) 端点是不是 per-book 覆盖过的
        if resolved.get("api_base_overridden"):
            findings.append(DriftFinding(
                book=book, kind="endpoint_overridden", severity="info",
                detail=f"api_base 被本书覆盖为 {api_base} (init 时落盘的地址, "
                       f"优先于 provider 注册表)",
                hint="确认这台机器上确实有这个端点",
                data={"api_base": api_base},
            ))

        # 3) 推理模型的 token 预算(信息项)
        if min_max_tokens:
            # 推理模型先花一大截预算写思维链: MiniMax M3.1 在 max_tokens=4096
            # 时 reasoning_tokens=4096 / content_len=0 —— API 正常返回 200,
            # 正文却是空的。所以这个下限必须让人看见, 而不是埋在注册表里。
            findings.append(DriftFinding(
                book=book, kind="token_budget", severity="info",
                detail=f"provider={provider} 是推理模型, max_tokens 下限 = "
                       f"{min_max_tokens} (调用点请求更小的值会被抬到这个数)",
                hint="正文预算要在这之外, 否则会写出空章节",
                data={"min_max_tokens": min_max_tokens},
            ))

        # 4) 端点探测(可选, 有预算)
        if not probe:
            continue
        key = (provider, api_base, model)
        if key in probe_cache:
            cached = probe_cache[key]
        else:
            remaining = deadline - time.perf_counter()
            if remaining <= 0:
                findings.append(DriftFinding(
                    book=book, kind="probe_skipped", severity="warn",
                    detail=f"探测预算 {budget_sec:.0f}s 已用尽, {api_base} 没探到",
                    hint="单独跑一次本检查, 或调大 budget_sec",
                ))
                probe_cache[key] = None
                continue
            planned += 1
            probes += 1
            try:
                cached = _probe_one(resolved, min(float(timeout_sec), remaining))
            except Exception as e:     # 探测本身不该让 doctor 崩
                cached = DriftFinding(
                    book="", kind="resolve_error", severity="warn",
                    detail=f"探测异常 ({type(e).__name__}: {e})",
                )
            probe_cache[key] = cached
        if cached is not None:
            findings.append(cached._replace(book=book))

    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    by_kind = {k: "ok" for k in DRIFT_KINDS}
    for f in findings:
        prev = by_kind.get(f.kind, "ok")
        if _SEVERITY_STATUS.get(f.severity, "warn") == "fail" or prev == "fail":
            by_kind[f.kind] = "fail"
        elif f.severity == "warn" and prev == "ok":
            by_kind[f.kind] = "warn"

    # 耗时说明直接交给 CLI: 串行探测的上界是"去重后的端点数 × 单次超时",
    # 提前把这个数告诉用户, 书多的时候才知道自己在等什么。
    if not probe:
        time_note = "只做了本地静态检查, 未发任何网络请求(零费用)"
    else:
        worst = planned * float(timeout_sec) if planned else 0.0
        time_note = (f"探测 {probes} 个端点(按 provider+端点+模型去重), "
                     f"用时 {elapsed_ms / 1000:.1f}s; "
                     f"本轮上限 ≈ {worst:.0f}s ({planned} × {timeout_sec}s), "
                     f"总预算 {budget_sec:.0f}s")

    return DriftReport(
        books=len(books),
        findings=findings,
        by_kind=by_kind,
        probes=probes,
        probed=probe,
        elapsed_ms=elapsed_ms,
        time_note=time_note,
        ok=not any(f.severity == "fatal" for f in findings),
    )


# 接线说明(留给 CLI 层): run_all() 刻意**不**包含漂移检测, 因为它会发真实
# 网络请求(云端 provider 每次探测都要计费)。建议:
#     report = check_llm_config_drift(probe=args.deep)   # --deep 才探测
#     print(format_drift_report(report))
#     return 0 if report.ok else 1


def format_report(results: list[CheckResult]) -> str:
    icon = {"ok": "✅", "warn": "⚠ ", "fail": "❌"}
    lines = [f"=== novel doctor ({platform.system()} {platform.release()}) ===", ""]
    for r in results:
        lines.append(f"  {icon[r.status]} {r.name}: {r.detail}")
    fails = sum(1 for r in results if r.status == "fail")
    warns = sum(1 for r in results if r.status == "warn")
    ok = sum(1 for r in results if r.status == "ok")
    lines.append("")
    lines.append(f"  汇总: ✅ {ok}  ⚠ {warns}  ❌ {fails}")
    return "\n".join(lines)


def format_drift_report(report: DriftReport) -> str:
    """把 DriftReport 渲染成人能读的段落(CLI 直接 print 这个)。"""
    icon = {"fatal": "❌", "warn": "⚠ ", "info": "ℹ "}
    tag = {"fatal": "致命", "warn": "告警", "info": "信息"}
    lines = [f"=== 配置漂移检测 ({report.books} 本书) ===", ""]
    if not report.findings:
        lines.append("  ✅ 没有发现配置漂移")
    for f in report.findings:
        lines.append(f"  {icon[f.severity]} [{tag[f.severity]}] {f.book}: {f.detail}")
        if f.hint:
            lines.append(f"      → {f.hint}")
    lines.append("")
    lines.append(f"  汇总: ❌ {len(report.fatal)}  ⚠ {len(report.warns)}  "
                 f"ℹ {len(report.infos)}  (probes={report.probes})")
    lines.append(f"  耗时: {report.time_note}")
    return "\n".join(lines)

