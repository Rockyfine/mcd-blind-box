"""命令行入口。

设计要点：

- ``open`` 是**只读**主流程，产出揭晓卡后停下，永远不下单；
- ``order`` 是唯一会真实下单的子命令，**没有 ``--confirm`` 一律拦截**；
- 默认走离线 fixtures，必须显式 ``--live`` 才会联网，避免误触真实 Token 与真实下单。
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, List, Optional, Sequence, Tuple

from . import __version__, rules
from .backend import make_backend, resolve_token
from .constants import (
    ALLOWED_TOOLS,
    CHALLENGE_MODES,
    DEFAULT_ENDPOINT,
    EXIT_AUTH,
    EXIT_BLOCKED,
    EXIT_OK,
    EXIT_RATE_LIMIT,
    EXIT_UNSATISFIABLE,
    EXIT_USAGE,
    SCENE_TO_BE_TYPE,
    TOKEN_ENV_VAR,
    USED_TOOLS,
)
from .errors import (
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
from .pipeline import BlindBoxPipeline
from .reveal import card_to_dict, render_confirm_panel, render_reveal, render_share_text

PROG = "mcd-blind-box"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROG,
        description="麦门盲盒 —— 把麦当劳点餐变成一场可验证的开盲盒游戏（默认离线，不联网）。",
        epilog=(
            "离线示例：python -m mcd_blind_box open --offline --mode budget --budget 30 --seed 1789000000000\n"
            f"联网示例：export {TOKEN_ENV_VAR}=*** && python -m mcd_blind_box open --live --city 南京市 --keyword 新街口"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"{PROG} {__version__}")

    sub = parser.add_subparsers(dest="command", metavar="<command>")

    _add_common(_add_open(sub), include_order=False)
    _add_common(_add_order(sub), include_order=True)
    _add_common(_add_query(sub, "tools", "列出工具（离线读内置白名单，--live 时向服务端 tools/list）"), include_order=False)
    _add_common(_add_query(sub, "verify", "连通性与鉴权自检（握手 + 工具计数）"), include_order=False)

    return parser


def _add_open(sub: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = sub.add_parser("open", help="【只读】定位门店 → 抓菜单 → 开盲盒 → 算价用券 → 输出揭晓卡")
    return p


def _add_order(sub: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = sub.add_parser(
        "order",
        help="【写操作·真实下单】必须显式 --confirm；没有 --confirm 一律拦截",
    )
    p.add_argument(
        "--confirm",
        action="store_true",
        help="安全闸：不传这个参数就绝不会调用 create-order",
    )
    p.add_argument("--user-text", default=None, help="用一段用户原话走确认判定（比 --confirm 更严格）")
    p.add_argument("--take-way-code", default=None, help="到店场景的取餐编码；不传则取算价返回的 takeWayList[0].code")
    p.add_argument("--remark", default=None, help="订单备注，仅外送场景，最多 50 字")
    return p


def _add_query(sub: argparse._SubParsersAction, name: str, help_text: str) -> argparse.ArgumentParser:
    return sub.add_parser(name, help=help_text)


def _add_common(parser: argparse.ArgumentParser, *, include_order: bool) -> None:
    parser.add_argument("--scene", choices=sorted(SCENE_TO_BE_TYPE), default="pickup",
                        help="取餐场景：pickup=到店自取(beType=1) / dt=得来速(beType=5) / delivery=麦乐送(beType=2)")
    parser.add_argument("--mode", action="append", default=[], choices=list(CHALLENGE_MODES),
                        help="挑战模式，可重复叠加（blind/budget/calorie/taste）")
    parser.add_argument("--budget", type=float, default=None, help="预算上限（元），预算挑战用")
    parser.add_argument("--calorie-max", type=int, default=None, help="热量上限（大卡），热量挑战用")
    parser.add_argument("--taste", default=None, help="口味关键词（辣/鸡/牛/鱼/素/甜/咖啡）")
    parser.add_argument("--city", default=None, help="城市名（searchType=2 时必填）")
    parser.add_argument("--keyword", default=None, help="位置关键词（searchType=2 时必填）")
    parser.add_argument("--address-id", default=None, help="配送地址 ID（外送场景）")
    parser.add_argument("--store-code", default=None, help="直接指定门店编码，跳过门店搜索")
    parser.add_argument("--be-code", default=None, help="门店 beCode（仅 beType=5/2；beType=1 绝不能传）")
    parser.add_argument("--seed", type=int, default=None,
                        help="随机种子（毫秒时间戳）。传入即完全确定可复现；不传则用服务端时间")
    parser.add_argument("--with-mod", action="store_true", help="为可特调商品附上特调项（会调用 query-meal-detail）")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出结果")
    parser.add_argument("--share", action="store_true", help="额外输出一段可复制的分享文案")

    group = parser.add_mutually_exclusive_group()
    group.add_argument("--offline", dest="live", action="store_false",
                       help="【默认】使用仓库内 fixtures，不联网、不消耗 Token")
    group.add_argument("--live", dest="live", action="store_true",
                       help="联网调用真实 mcd-mcp（需要 Token）")
    parser.set_defaults(live=False)

    parser.add_argument("--fixtures", default=None, help="fixtures 目录（默认仓库根目录下的 fixtures/）")
    parser.add_argument("--token", default=None, help=f"MCP Token；默认读环境变量 {TOKEN_ENV_VAR}")
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT, help=f"MCP 端点，默认 {DEFAULT_ENDPOINT}")


# ---------------------------------------------------------------------------
# 命令实现
# ---------------------------------------------------------------------------
def _prepare(args: argparse.Namespace) -> Tuple[BlindBoxPipeline, Any]:
    """构建 backend + pipeline，并把「定位门店 → 抓菜单 → 开盒」跑完。"""
    backend = make_backend(
        offline=not args.live,
        fixtures_dir=args.fixtures,
        token=args.token,
        endpoint=args.endpoint,
    )
    pipeline = BlindBoxPipeline(backend, offline=not args.live)

    be_type = rules.be_type_for_scene(args.scene)
    pipeline.set_scene(be_type)

    search_type = 2 if (args.city and args.keyword) else 1
    pipeline.locate_store(
        store_code=args.store_code,
        be_code=args.be_code,
        search_type=search_type,
        city=args.city,
        keyword=args.keyword,
        address_id=args.address_id,
    )
    pipeline.fetch_menu()

    card = pipeline.open_box(
        modes=args.mode,
        budget_yuan=args.budget,
        calorie_cap=args.calorie_max,
        taste=args.taste,
        timestamp_ms=args.seed,
        with_modification=args.with_mod,
    )
    return pipeline, card


def _emit(text: str) -> None:
    sys.stdout.write(text + "\n")


def cmd_open(args: argparse.Namespace) -> int:
    _, card = _prepare(args)
    if args.json:
        _emit(json.dumps(card_to_dict(card), ensure_ascii=False, indent=2))
    else:
        _emit(render_reveal(card))
        if args.share:
            _emit("")
            _emit("【分享文案】")
            _emit(render_share_text(card))
    return EXIT_OK


def cmd_order(args: argparse.Namespace) -> int:
    # ---- 安全闸：没有 --confirm 就直接返回，绝不构建 backend、绝不调用 create-order ----
    if not args.confirm:
        _emit("【已拦截】order 子命令会真实下单，但你没有传 --confirm。")
        _emit("")
        _emit("建议流程：")
        _emit(f"  1) 先看揭晓卡： {PROG} open {' '.join(_replay_args(args))}")
        _emit(f"  2) 确认无误后： {PROG} order --confirm {' '.join(_replay_args(args))}")
        _emit("")
        _emit("本项目默认不下单。")
        return EXIT_BLOCKED

    pipeline, card = _prepare(args)
    take_way_code = args.take_way_code or ""
    if not take_way_code and pipeline.order_type == 1:
        take_way_code = rules.pick_take_way(card.price)

    _emit(render_confirm_panel(card, take_way_code=take_way_code, remark=args.remark or ""))
    _emit("")

    result = pipeline.order(
        confirm=True,
        user_text=args.user_text,
        take_way_code=args.take_way_code,
        remark=args.remark,
    )
    if args.json:
        _emit(json.dumps(result, ensure_ascii=False, indent=2))
        return EXIT_OK

    # 真实返回同样包了一层信封，所以订单字段在 data 里；
    # 万一上游换了形状，退回顶层字典而不是把订单号显示成「未返回」。
    data = result.get("data") if isinstance(result, dict) else None
    if not isinstance(data, dict):
        data = result if isinstance(result, dict) else {}

    _emit(f"下单成功：订单号 {data.get('orderId', '(未返回)')}")
    _emit(f"状态：{data.get('status', '未知')}")
    if data.get("source"):
        _emit(f"来源：{data['source']}")
    _emit("")
    _emit("下一步：扫码支付或打开麦当劳 APP 完成支付，之后可查询订单状态。")
    return EXIT_OK


def _replay_args(args: argparse.Namespace) -> List[str]:
    """把当前参数拼回去，方便用户直接复制下一条命令。"""
    parts: List[str] = ["--offline" if not args.live else "--live"]
    if args.scene != "pickup":
        parts += ["--scene", args.scene]
    for mode in args.mode:
        parts += ["--mode", mode]
    if args.budget is not None:
        parts += ["--budget", str(args.budget)]
    if args.calorie_max is not None:
        parts += ["--calorie-max", str(args.calorie_max)]
    if args.taste:
        parts += ["--taste", args.taste]
    if args.city:
        parts += ["--city", args.city]
    if args.keyword:
        parts += ["--keyword", args.keyword]
    if args.address_id:
        parts += ["--address-id", args.address_id]
    if args.store_code:
        parts += ["--store-code", args.store_code]
    if args.be_code:
        parts += ["--be-code", args.be_code]
    if args.seed is not None:
        parts += ["--seed", str(args.seed)]
    if args.with_mod:
        parts.append("--with-mod")
    return parts


def cmd_tools(args: argparse.Namespace) -> int:
    if args.live:
        backend = make_backend(
            offline=False, token=args.token, endpoint=args.endpoint
        )
        tools = backend.list_tools()
        _emit(f"服务端共有 {len(tools)} 个工具：")
        for tool in tools:
            _emit(f"  - {tool.get('name')}")
        return EXIT_OK

    _emit("离线模式：列出本项目使用的工具（真实工具名，共 35 个可用）")
    _emit("")
    for name in USED_TOOLS:
        _emit(f"  * {name}")
    return EXIT_OK


def cmd_verify(args: argparse.Namespace) -> int:
    """自检。离线时校验 fixtures 完整性，联网时做握手与鉴权探测。"""
    if not args.live:
        return _verify_offline(args)

    token = resolve_token(args.token)
    if not token:
        _emit(f"未提供 Token。请设置环境变量 {TOKEN_ENV_VAR} 或传入 --token。")
        _emit("Token 申请地址：https://open.mcd.cn/mcp")
        return EXIT_AUTH

    backend = make_backend(offline=False, token=token, endpoint=args.endpoint)
    info = backend.probe()

    _emit("【连通性自检】")
    _emit(f"端点            {info['endpoint']}")
    _emit(f"Token           {info['token']}（已掩码）")
    _emit(f"服务名          {info['serverName']}")
    _emit(f"服务版本        {info['serverVersion']}")
    _emit(f"协议版本        {info['protocolVersion']}")
    _emit(f"工具数          {info['toolCount']}（预期 {info['expectedToolCount']}：{'一致' if info['toolCountMatches'] else '不一致'}）")
    _emit(f"Session 回传    {'是' if info['sessionIdPresent'] else '否'}")
    _emit("")
    _emit("提示：本命令只做握手与工具枚举，不会调用任何工具、不会产生订单。")
    return EXIT_OK


def _verify_offline(args: argparse.Namespace) -> int:
    """离线自检：fixtures 齐不齐、能不能解析、依赖是不是真的零。"""
    from pathlib import Path

    from .constants import EXPECTED_FIXTURES
    from .fixtures_client import DEFAULT_FIXTURES_DIR

    root = Path(args.fixtures).resolve() if args.fixtures else DEFAULT_FIXTURES_DIR
    _emit("【离线自检】不联网、不消耗 Token")
    _emit(f"fixtures 目录   {root}")
    _emit("")

    missing: List[str] = []
    for name in EXPECTED_FIXTURES:
        path = root / name
        if not path.exists():
            missing.append(name)
            _emit(f"  [缺失] {name}")
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            keys = len(payload) if isinstance(payload, dict) else len(payload)
            _emit(f"  [正常] {name}（{keys} 个顶层键/项）")
        except (OSError, json.JSONDecodeError) as exc:
            missing.append(name)
            _emit(f"  [损坏] {name}：{exc}")

    _emit("")
    if missing:
        _emit(f"结论：fixtures 不完整（缺 {len(missing)} 个：{'、'.join(missing)}）。")
        return EXIT_UNSATISFIABLE

    leaked = _third_party_imports()
    if _stdlib_check_supported():
        _emit(f"第三方依赖      {'无（仅标准库）' if not leaked else '发现：' + '、'.join(sorted(leaked))}")
    else:
        _emit("第三方依赖      本项自检已跳过（需要 Python 3.10+ 才能枚举标准库名单）")
    _emit(f"可用工具数      {len(ALLOWED_TOOLS)}")
    _emit("")
    _emit("结论：可以离线复现全部流程。跑 python -m mcd_blind_box open --offline 试试。")
    return EXIT_OK if not leaked else EXIT_USAGE


def _third_party_imports() -> List[str]:
    """扫描包内源码，找出标准库之外的 import。

    这是「零依赖」这条承诺的自证：任何第三方 import 都会被这里抓到，
    所以 README 上的说法不是嘴上说说。

    ``sys.stdlib_module_names`` 需要 Python 3.10+；更早的版本上这一项会返回空列表，
    调用方会据此在输出里注明「本项自检已跳过」，而不是假装通过。
    """
    import ast
    import sys
    from pathlib import Path

    stdlib = getattr(sys, "stdlib_module_names", None)
    if stdlib is None:  # Python < 3.10
        return []

    package_dir = Path(__file__).resolve().parent
    known = set(stdlib)
    found: List[str] = []
    for path in sorted(package_dir.glob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover - 源码语法错误不该发生
            continue
        for node in ast.walk(tree):
            roots: List[str] = []
            if isinstance(node, ast.Import):
                roots = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level:  # 相对导入，属于本包
                    continue
                if node.module:
                    roots = [node.module.split(".")[0]]
            for root in roots:
                if root in known or root == "mcd_blind_box":
                    continue
                if root not in found:
                    found.append(root)
    return found


def _stdlib_check_supported() -> bool:
    import sys

    return hasattr(sys, "stdlib_module_names")


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------
def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return EXIT_USAGE

    dispatch = {
        "open": cmd_open,
        "order": cmd_order,
        "tools": cmd_tools,
        "verify": cmd_verify,
    }

    try:
        return dispatch[args.command](args)
    except GateRejected as exc:
        _emit(f"【已拦截】{exc}")
        return EXIT_BLOCKED
    except AuthError as exc:
        _emit(f"【鉴权失败】{exc}")
        return EXIT_AUTH
    except RateLimitError as exc:
        _emit(f"【触发限流】{exc}")
        return EXIT_RATE_LIMIT
    except ConstraintUnsatisfiable as exc:
        _emit(f"【无法满足约束】{exc}")
        return EXIT_UNSATISFIABLE
    except (InvalidArgument, UnsupportedBeType, SceneMismatch) as exc:
        _emit(f"【参数问题】{exc}")
        return EXIT_USAGE
    except McpError as exc:
        _emit(f"【MCP 调用失败】{exc}")
        return 1
    except BlindBoxError as exc:
        _emit(f"【失败】{exc}")
        return 1
    except KeyboardInterrupt:  # pragma: no cover
        _emit("已中断。")
        return 130


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
