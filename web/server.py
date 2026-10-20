"""麦门盲盒 · 网页交互层的本地桥接后端。

职责单一：把浏览器发来的 JSON 请求，翻译成对 ``mcd_blind_box`` 的调用，
再把结果转成 JSON 回给前端。它是「静态网页」与「真实 mcd-mcp」之间的
唯一通道 —— 浏览器无法安全地直接持有 Token 调用 MCP，所以这里做一层
只读优先、写操作必过门禁的桥接。

设计要点（与项目铁律一一对应）：

- **零依赖**：只用标准库 ``http.server``，不引 Flask。
- **双模式**：``--offline`` 走 :class:`FixtureBackend`（fixtures，无需 Token），
  默认（``--live``）走 :class:`McpBackend`（需前端传入 Token）。
- **Token 不落盘**：Token 只从 POST body 读到局部变量，不写文件、不打日志、
  不 print；任何异常信息都先 ``mask_token`` 再返回。
- **只读开盒**：``/api/open`` 全程只读，绝不调用 ``create-order``。
- **下单双重门禁**：``/api/order`` 服务端先自判 ``confirm``，再交给
  ``pipeline.order(confirm=True)``（其内部还有 ``require_order_confirmation``）。
- **会话一次性**：开盒后把 pipeline/card 存入内存（TTL 15 分钟），
  下单成功即删除，避免「确认的是 A、下单的是 B」的歧义。

HTTP 业务逻辑抽成纯函数（``do_health`` / ``do_stores`` / ``do_open`` /
``do_order``），测试可直接注入 ``FixtureBackend`` 调用而无需起真实端口。
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import threading
import time
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import urlparse

# 让 ``import mcd_blind_box`` 能从仓库根目录解析到（web/ 在仓库根下）。
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mcd_blind_box import __version__  # noqa: E402
from mcd_blind_box.backend import Backend, make_backend  # noqa: E402
from mcd_blind_box.constants import DEFAULT_ENDPOINT, SCENE_TO_BE_TYPE  # noqa: E402
from mcd_blind_box.errors import (  # noqa: E402
    AuthError,
    BlindBoxError,
    ConstraintUnsatisfiable,
    GateRejected,
    InvalidArgument,
    McpError,
    RateLimitError,
    SceneMismatch,
    UnsupportedBeType,
)
from mcd_blind_box.pipeline import BlindBoxPipeline  # noqa: E402
from mcd_blind_box.reveal import card_to_dict, render_share_text  # noqa: E402
from mcd_blind_box.transport import mask_token  # noqa: E402

WEB_DIR = Path(__file__).resolve().parent
SESSION_TTL_SECONDS = 15 * 60


# ---------------------------------------------------------------------------
# 错误 → 统一信封
# ---------------------------------------------------------------------------
def _error(code: str, exit_code: int, message: str, *, retryable: bool = False) -> Dict[str, Any]:
    return {
        "ok": False,
        "error": {
            "code": code,
            "exit": exit_code,
            "message": message,
            "retryable": retryable,
        },
    }


def _map_exception(exc: BaseException) -> Dict[str, Any]:
    """把本项目异常映射到统一错误信封，复用 CLI 的 EXIT_* 语义。"""
    if isinstance(exc, AuthError):
        return _error("AUTH", 4, f"鉴权失败：{mask_token(str(exc))}")
    if isinstance(exc, RateLimitError):
        return _error("RATE_LIMIT", 5, f"触发限流：{exc}", retryable=True)
    if isinstance(exc, GateRejected):
        return _error("GATE", 3, str(exc))
    if isinstance(exc, ConstraintUnsatisfiable):
        return _error("UNSATISFIABLE", 6, str(exc), retryable=True)
    if isinstance(exc, (InvalidArgument, UnsupportedBeType, SceneMismatch)):
        return _error("USAGE", 2, str(exc))
    if isinstance(exc, (McpError, BlindBoxError)):
        return _error("MCP", 1, str(exc), retryable=True)
    return _error("MCP", 1, f"内部错误：{exc}", retryable=True)


def _http_status(error_code: str) -> int:
    return {
        "AUTH": 401,
        "USAGE": 400,
        "GATE": 403,
        "RATE_LIMIT": 429,
        "UNSATISFIABLE": 422,
        "MCP": 502,
    }.get(error_code, 502)


# ---------------------------------------------------------------------------
# 会话表（内存，TTL + 一次性）
# ---------------------------------------------------------------------------
class SessionStore:
    """保存「开盒 → 下单」之间的 pipeline/card 状态。

    只存内存、带 TTL、下单成功即删除。用 ``threading.Lock`` 保护，
    因为 ``ThreadingHTTPServer`` 会并发处理请求。
    """

    def __init__(self, ttl_seconds: int = SESSION_TTL_SECONDS) -> None:
        self._ttl = ttl_seconds
        self._lock = threading.Lock()
        self._sessions: Dict[str, Dict[str, Any]] = {}

    def put(self, pipeline: BlindBoxPipeline, card: Any) -> str:
        sid = secrets.token_hex(16)
        with self._lock:
            self._sessions[sid] = {
                "pipeline": pipeline,
                "card": card,
                "created": time.monotonic(),
            }
        return sid

    def get(self, sid: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            session = self._sessions.get(sid)
            if session is None:
                return None
            if time.monotonic() - session["created"] > self._ttl:
                self._sessions.pop(sid, None)
                return None
            return session

    def pop(self, sid: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self._sessions.pop(sid, None)


# ---------------------------------------------------------------------------
# 业务纯函数（可测试）
# ---------------------------------------------------------------------------
def build_backend(offline: bool, token: Optional[str]) -> Backend:
    """按模式构建数据源。联网缺 Token 时抛 InvalidArgument（映射为 USAGE）。"""
    if offline:
        return make_backend(offline=True)
    if not token:
        raise InvalidArgument(
            "联网模式需要 Token：请在页面顶部填入 MCP Token。"
            "（申请地址 https://open.mcd.cn/mcp）"
        )
    return make_backend(offline=False, token=token, endpoint=DEFAULT_ENDPOINT)


def _resolve_offline(
    handler_offline: bool,
    body: Dict[str, Any],
) -> bool:
    """决定本次请求走离线还是联网。

    优先级：请求体显式 ``offline`` 字段 > 服务端启动时的 ``--offline``。

    这样后端可以以「混合模式」运行：默认按启动参数，但前端每次请求
    都能通过 ``body["offline"]`` 显式切换 —— 提供 Token 时切到联网、
    未提供时切回离线演示。这是修复「填了 Token 仍显示离线」的关键。
    """
    if "offline" in body:
        return bool(body["offline"])
    return handler_offline


def _offline_store_codes(backend: Backend) -> Set[str]:
    """离线模式下真正能开出盒的门店。

    fixtures 的门店列表是服务端响应原样录制（到店 5 家），但菜单只录了其中 4 家。
    把没有菜单的门店摆在页面上，用户点下去只会得到 FixturesMissingError ——
    离线演示里这是最刺眼的失败，所以在列表层就过滤掉。
    """
    fixtures_dir = getattr(backend, "dir", None)
    if fixtures_dir is None:
        return set()
    try:
        table = json.loads((Path(fixtures_dir) / "meals.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    return {str(k) for k in table} if isinstance(table, dict) else set()


def do_stores(
    offline: bool,
    token: Optional[str],
    body: Dict[str, Any],
) -> Dict[str, Any]:
    """查门店。scene: pickup|dt|delivery。"""
    scene = str(body.get("scene") or "").strip()
    if scene not in SCENE_TO_BE_TYPE:
        raise InvalidArgument(f"未知场景 {scene!r}；可选 {sorted(SCENE_TO_BE_TYPE)}")

    backend = build_backend(offline, token)
    be_type = SCENE_TO_BE_TYPE[scene]

    rows: List[Dict[str, Any]] = []
    if be_type == 2:
        address_id = str(body.get("addressId") or "").strip()
        if not address_id:
            raise InvalidArgument("麦乐送场景必须提供 addressId")
        raw = backend.call_tool(
            "delivery-query-stores", {"beType": be_type, "addressId": address_id}
        )
        rows = _extract_list(raw, ("stores", "data", "list"))
    else:
        search_type = int(body.get("searchType") or 1)
        args: Dict[str, Any] = {"beType": be_type, "searchType": search_type}
        if search_type == 2:
            city = str(body.get("city") or "").strip()
            keyword = str(body.get("keyword") or "").strip()
            if not city or not keyword:
                raise InvalidArgument("按位置搜索必须同时提供 city 与 keyword")
            args["city"] = city
            args["keyword"] = keyword
        raw = backend.call_tool("query-nearby-stores", args)
        rows = _extract_list(raw, ("stores", "data", "list"))

    from mcd_blind_box.models import Store

    stores = [Store.from_api(r, be_type) for r in rows]
    if offline:
        available = _offline_store_codes(backend)
        if available:
            stores = [s for s in stores if s.store_code in available]
    return {
        "ok": True,
        "stores": [
            {
                "storeCode": s.store_code,
                "storeName": s.store_name,
                "beType": s.be_type,
                "beCode": s.be_code,
            }
            for s in stores
            if s.store_code
        ],
    }


def do_addresses(offline: bool, token: Optional[str]) -> Dict[str, Any]:
    """查配送地址（麦乐送场景）。"""
    backend = build_backend(offline, token)
    raw = backend.call_tool("delivery-query-addresses", {})
    rows = _extract_list(raw, ("addresses", "data", "list"))
    addresses = [
        {
            "addressId": str(r.get("addressId") or ""),
            "contactName": str(r.get("contactName") or ""),
            "phone": str(r.get("phone") or ""),
            "fullAddress": str(r.get("fullAddress") or r.get("address") or ""),
        }
        for r in rows
        if r.get("addressId")
    ]
    return {"ok": True, "addresses": addresses}


def do_open(
    offline: bool,
    token: Optional[str],
    body: Dict[str, Any],
    sessions: SessionStore,
) -> Dict[str, Any]:
    """开盒：定位门店 → 抓菜单 → 抽组合 → 算价用券 → 产出揭晓卡。全程只读。"""
    scene = str(body.get("scene") or "").strip()
    if scene not in SCENE_TO_BE_TYPE:
        raise InvalidArgument(f"未知场景 {scene!r}")

    store = body.get("store") or {}
    store_code = str(store.get("storeCode") or "").strip()
    store_name = str(store.get("storeName") or "").strip()
    be_code = store.get("beCode") or None
    address_id = body.get("addressId") or None

    backend = build_backend(offline, token)
    pipeline = BlindBoxPipeline(backend, offline=offline)
    pipeline.set_scene(SCENE_TO_BE_TYPE[scene])
    pipeline.locate_store(
        store_code=store_code or None,
        be_code=be_code,
        address_id=address_id,
    )

    # locate_store(store_code=...) 会生成占位店名，这里用前端传来的真实店名覆盖。
    if store_name and pipeline.store is not None:
        pipeline.store = replace(pipeline.store, store_name=store_name)

    pipeline.fetch_menu()

    modes = [str(m) for m in (body.get("modes") or [])]
    timestamp_ms = body.get("timestampMs")
    if offline and not timestamp_ms:
        # 离线 fixtures 里的 now-time-info 是固定录制值，直接回放会让每次开盒
        # 得到同一套组合 —— 在演示页面上这就是「假随机」。这里改用真实时间戳：
        # 每次都不一样，而卡片上的种子仍能拿回 CLI 用 --seed 复跑出同一结果。
        timestamp_ms = int(time.time() * 1000)
    card = pipeline.open_box(
        modes=modes,
        budget_yuan=body.get("budgetYuan"),
        calorie_cap=body.get("calorieCap"),
        taste=body.get("taste") or None,
        timestamp_ms=timestamp_ms,
        with_modification=bool(body.get("withModification")),
    )

    sid = sessions.put(pipeline, card)
    return {
        "ok": True,
        "sessionId": sid,
        "card": card_to_dict(card),
        "shareText": render_share_text(card),
    }


def do_order(
    body: Dict[str, Any],
    sessions: SessionStore,
) -> Dict[str, Any]:
    """真实下单。写操作，必须 confirm 且会话有效。"""
    sid = str(body.get("sessionId") or "").strip()
    if not sid:
        raise GateRejected("缺少 sessionId，请先开盒。")

    session = sessions.get(sid)
    if session is None:
        raise GateRejected("会话已过期或不存在，请重新开盒。")

    # 服务端自判 confirm（不依赖前端弹窗）—— 双重门禁第一道。
    if not bool(body.get("confirm")):
        raise GateRejected("未确认下单：请在弹窗中明确确认后再下单。")

    pipeline: BlindBoxPipeline = session["pipeline"]
    take_way_code = body.get("takeWayCode") or None
    remark = body.get("remark") or None

    result = pipeline.order(
        confirm=True,
        take_way_code=take_way_code,
        remark=remark,
    )

    # 下单成功即删除会话（一次性）。
    sessions.pop(sid)

    data = result.get("data") if isinstance(result, dict) else None
    if not isinstance(data, dict):
        data = result if isinstance(result, dict) else {}
    return {
        "ok": True,
        "order": {
            "orderId": data.get("orderId"),
            "status": data.get("status"),
            "source": data.get("source"),
            "offline": bool(data.get("offline")),
        },
        "message": "下单成功，请打开麦当劳 App 或小程序扫码支付。",
    }


def do_order_status(
    offline: bool,
    token: Optional[str],
    body: Dict[str, Any],
) -> Dict[str, Any]:
    """查询订单支付/配送状态（只读，走 query-order）。"""
    order_id = str(body.get("orderId") or "").strip()
    if not order_id:
        raise InvalidArgument("查询订单状态需要 orderId")

    backend = build_backend(offline, token)
    raw = backend.call_tool("query-order", {"orderId": order_id})

    data = raw.get("data") if isinstance(raw, dict) else None
    if not isinstance(data, dict):
        data = raw if isinstance(raw, dict) else {}
    return {
        "ok": True,
        "order": {
            "orderId": data.get("orderId", order_id),
            "status": data.get("status"),
            "offline": bool(data.get("offline")),
            "note": data.get("note"),
        },
    }


def do_health(offline: bool, uptime: float) -> Dict[str, Any]:
    return {
        "ok": True,
        "version": __version__,
        "mode": "offline" if offline else "live",
        "uptimeSec": int(time.monotonic() - uptime),
    }
def _content_type(suffix: str) -> str:
    return {
        ".html": "text/html; charset=utf-8",
        ".css": "text/css; charset=utf-8",
        ".js": "application/javascript; charset=utf-8",
        ".json": "application/json; charset=utf-8",
        ".svg": "image/svg+xml",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
    }.get(suffix, "application/octet-stream")


def _extract_list(raw: Any, keys: Tuple[str, ...]) -> List[Dict[str, Any]]:
    """从 MCP 返回里挖出对象列表（复用 pipeline 的逐层查找逻辑，这里单独一份）。"""
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    if not isinstance(raw, dict):
        return []
    for key in keys:
        value = raw.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    data = raw.get("data")
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        for key in keys:
            value = data.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


# ---------------------------------------------------------------------------
# HTTP 层
# ---------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    """薄 HTTP 层：路由 + JSON 解析 + 错误封装。业务在纯函数里。"""

    offline = False
    sessions: SessionStore = SessionStore()
    _start = time.monotonic()

    server_version = "mcd-blind-box-web/1.0"

    # 日志静默：Token 绝不能进日志，这里干脆不打访问日志。
    def log_message(self, *args: Any) -> None:
        return None

    def _send_json(self, payload: Dict[str, Any], status: int = 200) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()
        self.wfile.write(data)

    def _read_body(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise InvalidArgument("请求体不是合法的 JSON")
        if not isinstance(payload, dict):
            raise InvalidArgument("请求体必须是 JSON 对象")
        return payload

    def _dispatch(self) -> Tuple[Any, int]:
        path = urlparse(self.path).path

        if self.command == "OPTIONS":
            return {"ok": True}, 200

        if path == "/api/health" and self.command == "GET":
            return do_health(self.offline, self._start), 200

        if path == "/api/addresses" and self.command == "POST":
            body = self._read_body()
            offline = _resolve_offline(self.offline, body)
            return do_addresses(offline, body.get("token")), 200

        if path == "/api/stores" and self.command == "POST":
            body = self._read_body()
            offline = _resolve_offline(self.offline, body)
            return do_stores(offline, body.get("token"), body), 200

        if path == "/api/open" and self.command == "POST":
            body = self._read_body()
            offline = _resolve_offline(self.offline, body)
            return do_open(offline, body.get("token"), body, self.sessions), 200

        if path == "/api/order" and self.command == "POST":
            body = self._read_body()
            return do_order(body, self.sessions), 200

        if path == "/api/order-status" and self.command == "POST":
            body = self._read_body()
            offline = _resolve_offline(self.offline, body)
            return do_order_status(offline, body.get("token"), body), 200

        # 静态文件服务：只允许 GET，且路径限制在 web/ 目录内（防目录穿越）
        if self.command == "GET":
            return self._serve_static(path)

        return _error("USAGE", 2, f"未知接口 {path}"), 404

    def _serve_static(self, path: str) -> Tuple[bytes, str, str]:
        """返回 (内容字节, content_type, 真实路径)。找不到则抛 InvalidArgument。"""
        if path in ("/", ""):
            path = "/index.html"
        # 归一化 + 防目录穿越
        rel = path.lstrip("/")
        target = (WEB_DIR / rel).resolve()
        if WEB_DIR not in target.parents and target != WEB_DIR:
            raise InvalidArgument("非法路径")
        if not target.is_file():
            raise InvalidArgument(f"文件不存在：{path}")
        content = target.read_bytes()
        ctype = _content_type(target.suffix)
        return content, ctype, str(target)

    def do_GET(self) -> None:
        self._route()

    def do_POST(self) -> None:
        self._route()

    def do_OPTIONS(self) -> None:
        self._route()

    def _route(self) -> None:
        try:
            result = self._dispatch()
            # 静态文件：3 元组 (bytes, ctype, path)；JSON API：2 元组 (dict, status)
            if (
                isinstance(result, tuple)
                and len(result) == 3
                and isinstance(result[0], (bytes, bytearray))
            ):
                content, ctype, _ = result
                self._send_bytes(content, ctype)
            else:
                payload, status = result
                self._send_json(payload, status)
        except BlindBoxError as exc:
            err = _map_exception(exc)
            self._send_json(err, _http_status(err["error"]["code"]))
        except Exception as exc:  # pragma: no cover - 兜底
            err = _map_exception(exc)
            self._send_json(err, _http_status(err["error"]["code"]))

    def _send_bytes(self, content: bytes, ctype: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(content)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="mcd-blind-box-web",
        description="麦门盲盒网页交互层的本地桥接后端（仅标准库）。",
    )
    # 云托管（反向代理）会把端口与绑定地址注入 PORT / HOST 环境变量；
    # 本地开发时两者都不存在，仍然只监听 127.0.0.1:8787。
    parser.add_argument(
        "--host",
        default=os.environ.get("HOST") or "127.0.0.1",
        help="监听地址（默认仅本机；云托管时由 HOST 环境变量注入）",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("PORT") or 8787),
        help="监听端口（默认 8787；云托管时由 PORT 环境变量注入）",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="离线演示模式：走 fixtures，无需 Token（标注为演示数据）",
    )
    args = parser.parse_args(argv)

    Handler.offline = args.offline
    Handler.sessions = SessionStore()

    if args.host not in ("127.0.0.1", "localhost", "::1"):
        print("⚠️  警告：你正在把服务暴露到非本机地址，任何能访问该端口的人都能使用你的 Token。")
        print("    建议仅在明确需要远程访问时才这样做，并确保端口受防火墙保护。")

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    mode = "离线演示（fixtures，无需 Token）" if args.offline else "联网（需在页面填 Token）"
    print(f"麦门盲盒网页后端已启动：http://{args.host}:{args.port}/")
    print(f"模式：{mode}")
    print("按 Ctrl+C 停止。Token 仅在内存中，关闭即失效，不落盘。")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
