#!/usr/bin/env python3
"""离线冒烟：把 4 模式 × 3 链路和安全闸跑一遍，只看退出码对不对。

零依赖（只用标准库 subprocess）。它的作用是让人在 clone 之后
**一条命令**就能确认这个仓库是能跑的，而不是只能信 README。

用法::

    python scripts/smoke_offline.py

退出码 0 表示全部通过。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# (说明, 参数, 期望退出码)
CASES = [
    ("纯盲盒 / 到店自取", ["open", "--offline", "--scene", "pickup", "--mode", "blind", "--seed", "1789000000000"], 0),
    ("纯盲盒 / 得来速", ["open", "--offline", "--scene", "dt", "--mode", "blind", "--seed", "1789000000000"], 0),
    ("纯盲盒 / 麦乐送", ["open", "--offline", "--scene", "delivery", "--mode", "blind", "--seed", "1789000000000"], 0),
    ("预算挑战 / 到店", ["open", "--offline", "--scene", "pickup", "--mode", "budget", "--budget", "30", "--seed", "7"], 0),
    ("热量挑战 / 麦乐送", ["open", "--offline", "--scene", "delivery", "--mode", "calorie", "--calorie-max", "600", "--seed", "3"], 0),
    ("口味挑战 / 得来速", ["open", "--offline", "--scene", "dt", "--mode", "taste", "--taste", "辣", "--seed", "2"], 0),
    ("叠加模式 预算+热量", ["open", "--offline", "--scene", "pickup", "--mode", "budget", "--mode", "calorie", "--budget", "40", "--calorie-max", "800", "--seed", "11"], 0),
    ("特调 / 巨无霸", ["open", "--offline", "--scene", "pickup", "--seed", "11", "--with-mod"], 0),
    ("JSON 输出", ["open", "--offline", "--scene", "pickup", "--seed", "5", "--json"], 0),
    ("分享文案", ["open", "--offline", "--scene", "pickup", "--seed", "5", "--share"], 0),
    ("工具清单", ["tools", "--offline"], 0),
    ("离线自检", ["verify", "--offline"], 0),
    # 安全闸：没有 --confirm 一律拦截，且绝不联网
    ("下单缺 --confirm 被拦截", ["order", "--offline", "--scene", "pickup", "--seed", "1789000000000"], 3),
    ("离线下单带 --confirm", ["order", "--offline", "--scene", "pickup", "--seed", "1789000000000", "--confirm"], 0),
    # 约束不可能满足
    ("热量上限 5 大卡无解", ["open", "--offline", "--scene", "pickup", "--mode", "calorie", "--calorie-max", "5"], 6),
    ("预算 5 元如实放宽", ["open", "--offline", "--mode", "budget", "--budget", "5", "--seed", "42"], 0),
    # 参数错误
    ("未知口味", ["open", "--offline", "--mode", "taste", "--taste", "意面"], 2),
    ("到店场景传 beCode", ["open", "--offline", "--scene", "pickup", "--be-code", "B999"], 2),
    ("自取单传备注被拒绝", ["order", "--offline", "--scene", "pickup", "--seed", "1", "--confirm", "--remark", "放前台"], 2),
    ("指定门店缺 beCode", ["open", "--offline", "--scene", "dt", "--store-code", "100003"], 2),
]


def main() -> int:
    python = sys.executable
    failures = []

    for label, argv, expected in CASES:
        proc = subprocess.run(
            [python, "-m", "mcd_blind_box", *argv],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        ok = proc.returncode == expected
        mark = "PASS" if ok else "FAIL"
        print(f"[{mark}] {label:28s} exit={proc.returncode} 期望={expected}")
        if not ok:
            failures.append((label, proc.returncode, expected, proc.stdout, proc.stderr))

    print()
    if failures:
        print(f"共 {len(failures)} 个用例未通过：")
        for label, got, expected, out, err in failures:
            print(f"\n--- {label}（exit={got}，期望 {expected}）---")
            print(out.strip() or "(无 stdout)")
            if err.strip():
                print(err.strip())
        return 1

    print(f"全部 {len(CASES)} 个用例通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
