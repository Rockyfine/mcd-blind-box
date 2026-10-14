"""规则铁律的纯函数实现。

这些函数是 Skill 里那些「踩过的坑」的代码化。每个函数都只做一件事，
不碰网络、不碰文件，因此可以被完全离线地单元测试。
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from .constants import (
    ALLOWED_TOOLS,
    ORDER_TYPE_BY_BE_TYPE,
    ORDER_TYPE_DELIVERY,
    ORDER_TYPE_IN_STORE,
    PRICE_UNIT_DIVISOR,
    REMARK_MAX_LEN,
    REQUIRES_BE_CODE,
    SCENE_TO_BE_TYPE,
    SUPPORTED_BE_TYPES,
)
from .errors import InvalidArgument, SceneMismatch, UnsupportedBeType
from .models import ModGroup, PriceResult


# ---------------------------------------------------------------------------
# 场景与枚举映射
# ---------------------------------------------------------------------------
def order_type_for(be_type: int) -> int:
    """beType → orderType 的固定映射。

    ``1/5 → 1``（到店），``2 → 2``（外送）。不支持的 beType 直接报错。
    """
    if be_type not in ORDER_TYPE_BY_BE_TYPE:
        raise UnsupportedBeType(
            f"不支持的 beType={be_type!r}；本项目仅支持 "
            f"{list(SUPPORTED_BE_TYPES)}（6=企业团餐属于其他玩法，未实现）"
        )
    return ORDER_TYPE_BY_BE_TYPE[be_type]


def requires_be_code(be_type: int) -> bool:
    """beType=5/2 需要 beCode；beType=1 不需要（传了会报错）。"""
    if be_type not in REQUIRES_BE_CODE:
        raise UnsupportedBeType(f"不支持的 beType={be_type!r}")
    return REQUIRES_BE_CODE[be_type]


def be_type_for_scene(scene: str) -> int:
    """CLI 的 --scene 取值 → beType。"""
    if scene not in SCENE_TO_BE_TYPE:
        raise UnsupportedBeType(
            f"未知场景 {scene!r}；可选 {sorted(SCENE_TO_BE_TYPE)}"
        )
    return SCENE_TO_BE_TYPE[scene]


def assert_no_be_code_for_pickup(be_type: int, params: Mapping[str, Any]) -> None:
    """到店自取（beType=1）绝不能携带 beCode —— 这是文档点名的头号坑。

    传了会被服务端拒绝，整条链路直接失败，所以在发请求前就硬拦。
    """
    if be_type == 1 and params.get("beCode"):
        raise SceneMismatch(
            "beType=1（到店自取）不能传 beCode，传了会报错。"
            "beCode 只属于 beType=5（得来速）和 beType=2（麦乐送）。"
        )


def build_scene_args(
    be_type: int,
    *,
    be_code: Optional[str] = None,
    order_type: Optional[int] = None,
) -> Dict[str, Any]:
    """构造所有点餐工具共用的场景参数，并在构造期就把规则校验掉。

    返回的 dict 一定满足：
    - ``orderType`` 与 ``beType`` 匹配固定映射
    - ``beType=1`` 时**不含** ``beCode`` 键
    - ``beType=5/2`` 时**一定含**非空 ``beCode``
    """
    if be_type not in SUPPORTED_BE_TYPES:
        raise UnsupportedBeType(
            f"不支持的 beType={be_type!r}；本项目仅支持 {list(SUPPORTED_BE_TYPES)}"
        )

    expected = ORDER_TYPE_BY_BE_TYPE[be_type]
    if order_type is None:
        order_type = expected
    elif order_type != expected:
        raise SceneMismatch(
            f"beType={be_type} 对应 orderType={expected}，收到 orderType={order_type}"
        )

    args: Dict[str, Any] = {"orderType": order_type, "beType": be_type}

    if REQUIRES_BE_CODE[be_type]:
        if not be_code:
            raise SceneMismatch(
                f"beType={be_type} 必须传 beCode（来自 "
                f"{'query-nearby-stores' if be_type == 5 else 'delivery-query-stores'}）"
            )
        args["beCode"] = be_code
    else:
        if be_code:
            raise SceneMismatch("beType=1（到店自取）不能传 beCode")
    return args


def build_store_locator_args(
    be_type: int,
    *,
    search_type: Optional[int] = None,
    city: Optional[str] = None,
    keyword: Optional[str] = None,
    address_id: Optional[str] = None,
) -> Dict[str, Any]:
    """构造门店定位工具的参数（query-nearby-stores / delivery-query-stores）。"""
    if be_type == 1 or be_type == 5:
        if search_type is None:
            search_type = 1
        args: Dict[str, Any] = {"beType": be_type, "searchType": search_type}
        if search_type == 2:
            if not city:
                raise InvalidArgument("searchType=2 时必须提供 city")
            if not keyword:
                raise InvalidArgument("searchType=2 时必须提供 keyword")
            args["city"] = city
            args["keyword"] = keyword
        return args

    if be_type == 2:
        if not address_id:
            raise InvalidArgument("麦乐送（beType=2）必须提供 addressId")
        return {"beType": be_type, "addressId": address_id}

    raise UnsupportedBeType(f"不支持的 beType={be_type!r}")


# ---------------------------------------------------------------------------
# 工具名白名单
# ---------------------------------------------------------------------------
def assert_tool_allowed(name: str) -> str:
    """拒绝任何不在官方 35 个工具里的名字。

    用来杜绝 ``query-nearby-store``（少了 s）、``delivery-query-address``（单数）
    这类拼错 —— 它们在文档描述里出现过，但真实工具名不是这样。
    """
    if name not in ALLOWED_TOOLS:
        raise InvalidArgument(f"{name!r} 不是 mcd-mcp 的真实工具名")
    return name


# ---------------------------------------------------------------------------
# 特调 key 铁律（SKILL.md 规则 6 / tool-contract.md 第 9 节）
# ---------------------------------------------------------------------------
def build_modification_values(
    groups: Iterable[ModGroup],
    chosen: Optional[Mapping[str, str]] = None,
) -> List[Dict[str, Any]]:
    """把特调选择转换成 ``modification.values[]``。

    铁律（calculate-price 与 create-order 完全一致，所以两处都调这个函数）：

    1. **选中项** → ``key`` 用它的 ``selected_key``；
    2. **未选中项**且 ``unselected_key`` 非空 → ``key`` 用 ``unselected_key``，
       **并且这一项也要传进去**；
    3. 推论：**只要某个组里存在带 unselected_key 的项，该组所有项都要传**
       （不管用户选没选）；
    4. key 为空的项直接跳过（等价于不改这一项）。

    ``chosen`` 是 ``{group_code: value_code}`` 的可选覆盖；不传则用组内默认项。
    返回项的 ``quantity``：选中项为 1，未选中项为 0。
    """
    chosen = chosen or {}
    values: List[Dict[str, Any]] = []

    for group in groups:
        if not group.values:
            continue

        has_unselected = group.has_unselected_key

        selected_value = None
        wanted_code = chosen.get(group.code)
        if wanted_code:
            for value in group.values:
                if value.code == wanted_code:
                    selected_value = value
                    break
        if selected_value is None:
            selected_value = group.default_value()

        for value in group.values:
            is_selected = selected_value is not None and value.code == selected_value.code

            if is_selected:
                key = value.selected_key
                include = True
                quantity = 1
            elif value.unselected_key:
                key = value.unselected_key
                include = True
                quantity = 0
            else:
                key = value.selected_key
                # 组内有 unselected_key 就必须整组传，否则只传选中项
                include = has_unselected
                quantity = 0

            if include and key:
                values.append({"code": value.code, "key": key, "quantity": quantity})

    return values


# ---------------------------------------------------------------------------
# 单位与格式
# ---------------------------------------------------------------------------
def fen_to_yuan(fen: Any) -> str:
    """分 → 元的展示字符串。价格字段单位是分，展示前必须 /100。"""
    try:
        amount = int(fen)
    except (TypeError, ValueError):
        amount = 0
    return f"{amount / PRICE_UNIT_DIVISOR:.2f}"


def yuan_to_fen(yuan: Any) -> int:
    """元 → 分，用于把 CLI 传入的预算换算成可比较的整数。"""
    try:
        return int(round(float(yuan) * PRICE_UNIT_DIVISOR))
    except (TypeError, ValueError):
        raise InvalidArgument(f"无法把 {yuan!r} 解析成金额")


# ---------------------------------------------------------------------------
# 备注与取餐方式
# ---------------------------------------------------------------------------
def validate_remark(remark: Optional[str], order_type: int) -> Optional[str]:
    """校验备注：最多 50 字，且**仅外送场景**引导填写。"""
    if remark is None:
        return None
    text = str(remark).strip()
    if not text:
        return None
    if order_type != ORDER_TYPE_DELIVERY:
        raise InvalidArgument("remark 仅外送场景（orderType=2）引导填写，自取单不要填")
    if len(text) > REMARK_MAX_LEN:
        raise InvalidArgument(f"remark 最多 {REMARK_MAX_LEN} 字，当前 {len(text)} 字")
    return text


def pick_take_way(price_result: PriceResult) -> str:
    """取餐方式只能取自 calculate-price 返回的 takeWayList[].code。"""
    code = price_result.first_take_way_code()
    if not code:
        raise SceneMismatch(
            "算价结果里没有 takeWayList[].code，无法下单。"
            "takeWayCode 不允许自行构造。"
        )
    return code


def require_address_id(order_type: int, address_id: Optional[str]) -> Optional[str]:
    """orderType=2 必填 addressId。"""
    if order_type == ORDER_TYPE_DELIVERY:
        if not address_id:
            raise InvalidArgument("orderType=2（外送）必填 addressId")
        return address_id
    return None


def build_items_payload(items: Sequence[Any]) -> List[Dict[str, Any]]:
    """批量把 CartItem 转成 items[]；calculate-price 与 create-order 共用。"""
    return [item.to_payload() for item in items]


def is_in_store(order_type: int) -> bool:
    return order_type == ORDER_TYPE_IN_STORE
