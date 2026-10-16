"""挑战模式、随机抽取与约束放宽。

随机策略必须**可验证**：种子取自 ``now-time-info`` 的毫秒时间戳，
候选集按 ``productCode`` 升序排序后取模。同样的种子必然得到同样的结果，
所以揭晓卡上写的那句「种子 → 第 N 项」是真的可以被复核的。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable, List, Optional, Sequence, Tuple

from .constants import (
    BUDGET_SKELETON,
    CANDIDATE_COUNT,
    CAT_DESSERT,
    CAT_DRINK,
    CAT_MAIN,
    CAT_SIDE,
    CHALLENGE_MODES,
    MODE_BLIND,
    MODE_BUDGET,
    MODE_CALORIE,
    MODE_TASTE,
    SEED_STRIDE,
    TASTE_KEYWORDS,
    TASTE_KEYWORDS_EXTRA,
    TASTE_NEGATIVE,
)
from .errors import ConstraintUnsatisfiable, InvalidArgument
from .models import Meal


@dataclass(frozen=True)
class Candidate:
    """一个候选抽取结果（主品 + 若干配品，已按约束筛过）。"""

    seed: int
    main: Meal
    picks: Tuple[Meal, ...] = ()
    main_index: int = 0
    main_pool_size: int = 0
    pool_size: int = 0
    rank: int = 0

    @property
    def second(self) -> Optional[Meal]:
        """第一个配品。``picks[0]`` 是主品，所以配品从 ``picks[1]`` 开始。"""
        return self.picks[1] if len(self.picks) > 1 else None


# ---------------------------------------------------------------------------
# 模式
# ---------------------------------------------------------------------------
def normalize_modes(modes: Iterable[str]) -> Tuple[str, ...]:
    """去重并保持顺序地规范化模式串（四种模式可以叠加，约束取交集）。"""
    result: List[str] = []
    for mode in modes or ():
        if mode not in CHALLENGE_MODES:
            raise InvalidArgument(
                f"未知挑战模式 {mode!r}；可选 {list(CHALLENGE_MODES)}"
            )
        if mode not in result:
            result.append(mode)
    return tuple(result or (MODE_BLIND,))


def has_mode(modes: Sequence[str], mode: str) -> bool:
    return mode in modes


# ---------------------------------------------------------------------------
# 口味筛选
# ---------------------------------------------------------------------------
def match_taste(meal_name: str, taste: str) -> bool:
    """餐品名是否命中口味（语义匹配：正向关键词 + 负向排除）。

    比「单纯字段匹配」更进一步：除了逐字表 ``TASTE_KEYWORDS``，还结合
    ``TASTE_KEYWORDS_EXTRA`` 的场景化关联词扩大命中面，再用 ``TASTE_NEGATIVE``
    剔除「命中正向词却实为另一品类」的干扰项（如「藤椒甜筒」不该算「辣」）。

    一个餐品若正向命中、但命中任一负向排除词，即判为不匹配 —— 保证口味
    挑战抽到的是真正符合该口味的餐品，而不是被同名子串误伤的结果。
    """
    keywords = TASTE_KEYWORDS.get(taste)
    if keywords is None:
        raise InvalidArgument(
            f"未知口味 {taste!r}；可选 {sorted(TASTE_KEYWORDS)}"
        )
    positive = tuple(keywords) + tuple(TASTE_KEYWORDS_EXTRA.get(taste, ()))
    if not any(keyword in meal_name for keyword in positive):
        return False
    negative = TASTE_NEGATIVE.get(taste, ())
    if any(word in meal_name for word in negative):
        return False
    return True


def filter_by_taste(meals: Sequence[Meal], taste: Optional[str]) -> List[Meal]:
    """按口味筛选候选；未指定口味时原样返回。"""
    if not taste:
        return list(meals)
    return [m for m in meals if match_taste(m.name, taste)]


def filter_by_calories(
    meals: Sequence[Meal],
    calories_by_code: dict,
    cap: Optional[int],
) -> List[Meal]:
    """按单品热量上限粗筛，减少后续组合试价次数。

    匹配不到热量的商品在这一步被**排除**（因为无法证明它满足约束），
    而不是当成 0 卡放行 —— 那等于编造数据。
    """
    if cap is None:
        return list(meals)
    result = []
    for meal in meals:
        value = calories_by_code.get(meal.product_code)
        if value is not None and value <= cap:
            result.append(meal)
    return result


# ---------------------------------------------------------------------------
# 随机抽取
# ---------------------------------------------------------------------------
def sorted_pool(meals: Sequence[Meal]) -> List[Meal]:
    """候选池的稳定排序：按 productCode 升序。

    这一步是「随机可复现」的前提 —— 没有稳定顺序，同样的种子会抽到不同结果。
    """
    return sorted(meals, key=lambda m: m.product_code)


def candidate_for_seed(
    pool: Sequence[Meal],
    seed: int,
    main_pool: Optional[Sequence[Meal]] = None,
    extras: int = 1,
    extra_filter: Optional[Callable[[Meal, Sequence[Meal]], bool]] = None,
    rank: int = 0,
) -> Candidate:
    """按规则从稳定候选池里抽一个组合。

    - **主品**从 ``main_pool``（主食类）里取，索引 ``i = seed mod N``（N = 主食数）；
    - **配品**种子用 ``floor(seed / N)``（第 2 个配品用 ``floor(seed / N²)``，依此类推），
      在**剔除已选项**的候选集里重复同样的取模，并且**剔除已选项**后取模；
    - ``extra_filter(meal, picked_so_far)`` 在抽取**之前**淘汰不满足约束的配品 ——
      这正是规则要求的「先淘汰不符合约束的候选，只在剩余集合里抽」。
    - ``rank`` 记录这是第几个候选（0 = 原始种子那一个）。约束不满足时管线会顺延到
      后续候选，然后把真实生效的 rank 如实标出来，避免用户以为看到的就是原始抽取。

    **关于配品池的一处取舍**：``challenge-rules.md`` 第 25 行字面上说配品在
    「剔除主品后的候选集」里抽，那个集合是含其他主食的；但同文件第三节的
    「组合骨架」明确把配品定义为小食 / 薯条 / 饮料，揭晓卡也是按「主品 / 配品」
    两栏渲染的。若照字面实现，菜单里约四成种子会抽出「两个汉堡」，其中一个还
    被标成「配品」——玩家一眼就会觉得是 bug。

    所以这里的实现是：配品**优先**在非主食类里抽；只有当非主食类为空、或都被
    ``extra_filter`` 淘汰掉时，才回退到含主食的完整剔除集。这样既保住骨架语义，
    又不会在「整本菜单都是主食」这类极端门店上直接开不出盒。
    """
    full = sorted_pool(pool)
    if not full:
        raise ConstraintUnsatisfiable("候选集为空，无法开盒")

    mains = sorted_pool(main_pool) if main_pool else full
    if not mains:
        mains = full

    n = len(mains)
    main_index = seed % n
    main = mains[main_index]

    picked: List[Meal] = [main]
    taken = {main.product_code}
    rest = _order_for_pairing([m for m in full if m.product_code not in taken])

    for level in range(1, max(extras, 0) + 1):
        if not rest:
            break

        def _drawable(seq: Sequence[Meal]) -> List[Meal]:
            return [
                m for m in seq if extra_filter is None or extra_filter(m, picked)
            ]

        # 优先非主食；非主食为空或被约束全部淘汰时，才回退到含主食的集合
        allowed = _drawable([m for m in rest if m.category != CAT_MAIN])
        if not allowed:
            allowed = _drawable(rest)
        if not allowed:
            break

        divisor = n ** level
        chosen = allowed[(seed // divisor) % len(allowed)]
        picked.append(chosen)
        taken.add(chosen.product_code)
        rest = [m for m in rest if m.product_code not in taken]

    head, tail = picked[0], picked[1:]
    tail.sort(key=lambda m: (_PAIRING_ORDER.get(m.category, 9), m.product_code))

    return Candidate(
        seed=seed,
        main=head,
        picks=tuple([head] + tail),
        main_index=main_index,
        main_pool_size=n,
        pool_size=len(full),
        rank=rank,
    )


_PAIRING_ORDER = {CAT_SIDE: 0, CAT_DESSERT: 1, CAT_DRINK: 2, CAT_MAIN: 3}


def _order_for_pairing(meals: Sequence[Meal]) -> List[Meal]:
    """配品的稳定排序：小食优先，其次甜点、饮料，最后才是主食。"""
    return sorted(meals, key=lambda m: (_PAIRING_ORDER.get(m.category, 9), m.product_code))


def generate_candidates(
    pool: Sequence[Meal],
    timestamp_ms: int,
    count: int = CANDIDATE_COUNT,
    main_pool: Optional[Sequence[Meal]] = None,
    extras: int = 1,
    extra_filter: Optional[Callable[[Meal, Sequence[Meal]], bool]] = None,
) -> List[Candidate]:
    """生成若干候选组合，供约束筛选与逐级放宽使用。

    第 0 个候选就是「规则本来的那个」：种子 = 服务端毫秒时间戳。
    后续候选用质数步长派生新种子，避免永远抽到同一套。
    """
    if count < 1:
        raise InvalidArgument("count 必须 ≥ 1")
    return [
        candidate_for_seed(
            pool,
            timestamp_ms + i * SEED_STRIDE,
            main_pool=main_pool,
            extras=extras,
            extra_filter=extra_filter,
            rank=i,
        )
        for i in range(count)
    ]


def build_combo_meals(candidate: Candidate, pool: Sequence[Meal] = (), extras: int = 1) -> List[Meal]:
    """返回候选已经定好的组合。

    参数 ``pool`` / ``extras`` 保留是为了向后兼容；抽取逻辑已经全部在
    :func:`candidate_for_seed` 里完成（因为约束必须在抽取**之前**生效）。
    """
    return list(candidate.picks) or [candidate.main]


# ---------------------------------------------------------------------------
# 组合骨架
# ---------------------------------------------------------------------------
def skeleton_for_budget(budget_yuan: Optional[float]) -> Tuple[int, int, int]:
    """预算区间 → 组合骨架 ``(主品数, 小食数, 饮料数)``。

    参考区间，不是硬规则 —— 实际以真实菜单与价格为准。
    """
    if budget_yuan is None:
        return (1, 1, 0)
    for upper, mains, sides, drinks in BUDGET_SKELETON:
        if budget_yuan <= upper:
            return (mains, sides, drinks)
    return (1, 1, 1)


def extra_item_count(budget_yuan: Optional[float]) -> int:
    """骨架里除主品外还要抽几件。"""
    mains, sides, drinks = skeleton_for_budget(budget_yuan)
    total = max(mains, 1) + sides + drinks
    return max(total - 1, 0)


def pick_main_categories(pool: Sequence[Meal]) -> List[Meal]:
    """只取主食类候选（有分类信息时用它做口味挑战的主品池）。"""
    mains = [m for m in pool if m.category == CAT_MAIN]
    return mains or list(pool)


def calorie_extras_filter(
    cap: Optional[int],
    calories_by_code: dict,
) -> Optional[Callable[[Meal, Sequence[Meal]], bool]]:
    """构造「配品是否还能塞进热量上限」的判定函数。

    这个函数在抽取**之前**使用：只有整组（已选项 + 待选项）总热量
    仍在上限内，才允许该配品进入抽取池。所以热量挑战不会先抽一套
    超限组合、再靠事后放宽来自圆其说。

    匹配不到热量的餐品一律判否 —— 无法证明满足约束的，就不该被选中。
    """
    if cap is None:
        return None

    def _ok(meal: Meal, picked: Sequence[Meal]) -> bool:
        own = calories_by_code.get(meal.product_code)
        if own is None or own > cap:
            return False
        total = own
        for item in picked:
            value = calories_by_code.get(item.product_code)
            if value is None:
                return False
            total += value
        return total <= cap

    return _ok


def budget_extras_filter(
    cap_fen: Optional[int],
    payable_fn: Callable[[int, Sequence[Meal]], int],
) -> Optional[Callable[[Meal, Sequence[Meal]], bool]]:
    """构造「配品加进来后、券后价是否还超预算」的判定函数。

    ``payable_fn(subtotal_fen, picked) -> payable_fen`` 由调用方提供（管线里就是
    「用真实券表挑最优券再算券后价」），所以这里的判断和最终算价用的是同一套
    逻辑，而不是拍脑袋留个安全边际。

    为什么必须在抽取**之前**做：``challenge-rules.md`` 第二节的放宽顺序第一条
    明确写着「先淘汰不符合约束的候选，只在剩余集合里抽」。如果只在抽完之后
    评估，预算挑战会经常抽出一套明明可以避开的超预算组合，然后靠「已放宽」
    来兜底 —— 那既不必要，也让用户觉得这把不算数。
    """
    if cap_fen is None:
        return None

    def _ok(meal: Meal, picked: Sequence[Meal]) -> bool:
        subtotal = meal.price_fen + sum(item.price_fen for item in picked)
        return payable_fn(subtotal, list(picked) + [meal]) <= cap_fen

    return _ok


def combine_filters(
    *filters: Optional[Callable[[Meal, Sequence[Meal]], bool]],
) -> Optional[Callable[[Meal, Sequence[Meal]], bool]]:
    """把多个配品过滤器取交集；全为 None 时返回 None。"""
    active = [f for f in filters if f is not None]
    if not active:
        return None
    if len(active) == 1:
        return active[0]

    def _ok(meal: Meal, picked: Sequence[Meal]) -> bool:
        return all(f(meal, picked) for f in active)

    return _ok


def style_key_for(name: str, category: str) -> str:
    """把「名称 + 品类归属」归一成一个样式键（去重判定依据）。

    用「去壳后的名称」：去掉规格/量词前缀（中/大/小等）与尾缀（杯/块/份），
    让「中薯条」和「大薯条」归一成同一个样式「薯条」，「可口可乐中杯」归一成「可口可乐」。
    再叠加品类归属作为最终键：名称去不干净时，品类归属兜底（同为 side 也判重复）。
    """
    stripped = _strip_style_words(name or "")
    return f"{category}:{stripped}"


def style_key(meal: Meal) -> str:
    """餐品的样式键 —— :func:`style_key_for` 的便捷包装。"""
    return style_key_for(meal.name or meal.product_code, meal.category)


_STYLE_PREFIXES = ("中", "大", "小", "超大", "超值")
_STYLE_SUFFIXES = ("杯", "块", "份", "对", "桶", "盒")


def _strip_style_words(name: str) -> str:
    """把「中薯条」→「薯条」、「可口可乐中杯」→「可口可乐」，供样式归一。

    只做**纯字面**的前缀/尾缀剥离，不引入任何词库 —— 剥不干净也没关系，
    因为 :func:`dedup_extras_filter` 还有「品类归属相同」这一层兜底。
    """
    text = name
    for suffix in _STYLE_SUFFIXES:
        if text.endswith(suffix) and len(text) > len(suffix):
            text = text[: -len(suffix)]
            break
    for prefix in _STYLE_PREFIXES:
        if text.startswith(prefix) and len(text) > len(prefix):
            text = text[len(prefix):]
            break
    return text


def dedup_extras_filter(
    occupied_styles: Sequence[str],
) -> Optional[Callable[[Meal, Sequence[Meal]], bool]]:
    """构造「配品不与已占用样式重复」的抽取前过滤器。

    ``occupied_styles`` 是已占用餐品的样式键集合（由主品及其套餐内部默认子项、
    以及已抽配品共同构成）。凡配品的样式键与已占用集相交，就在抽取**之前**被淘汰，
    从而默认盲盒整体不会出现「套餐里已有一杯薯条、配品又抽一杯薯条」这类样式重复。

    这是「抽取前淘汰、只在剩余集合里抽」这条铁律在「去重」维度上的落实：
    不是抽完再删，而是压根不让重复样式进入候选池。
    """
    if not occupied_styles:
        return None
    occupied = frozenset(occupied_styles)

    def _ok(meal: Meal, picked: Sequence[Meal]) -> bool:
        return style_key(meal) not in occupied

    return _ok


# ---------------------------------------------------------------------------
# 逐级放宽
# ---------------------------------------------------------------------------
def relax_budget(
    entries: Sequence[Tuple[object, int]],
    cap_fen: int,
) -> Tuple[object, int]:
    """预算挑战的放宽：在所有候选里取「券后价最接近上限」的那个。

    ``entries`` 是 ``(payload, payable_fen)`` 列表。
    """
    if not entries:
        raise ConstraintUnsatisfiable("没有可用于放松约束的候选组合")
    return min(entries, key=lambda pair: (abs(pair[1] - cap_fen), pair[1]))


def relax_calorie(entries: Sequence[Tuple[object, int]]) -> Tuple[object, int]:
    """热量挑战的放宽：取总热量最低的那个。"""
    if not entries:
        raise ConstraintUnsatisfiable("没有可用于放松约束的候选组合")
    return min(entries, key=lambda pair: pair[1])


def budget_cap_fen(budget_yuan: Optional[float]) -> Optional[int]:
    if budget_yuan is None:
        return None
    return int(round(float(budget_yuan) * 100))


def requires_pricing(modes: Sequence[str]) -> bool:
    """哪些模式需要先算价才能判断是否满足（预算挑战）。"""
    return has_mode(modes, MODE_BUDGET)


def describe_modes(modes: Sequence[str], taste: Optional[str] = None) -> str:
    """把模式列表拼成一句人话；口味只标注在「口味挑战」上，不要污染其他标签。"""
    from .constants import MODE_LABELS

    labels: List[str] = []
    for mode in modes:
        label = MODE_LABELS.get(mode, mode)
        if mode == MODE_TASTE and taste:
            label = f"{label}（{taste}）"
        labels.append(label)
    return " + ".join(labels)
