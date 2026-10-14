#!/usr/bin/env bash
# 把「麦门盲盒」安装成本机的 WorkBuddy Skill。
#
# 做的事只有一件：把仓库里的 skill/mcd-blind-box-challenge/ 复制到
# ~/.workbuddy/skills/ 下。宿主会自动扫描该目录里的 SKILL.md，**无需注册**。
#
# 用法：
#   bash scripts/install_skill.sh                 # 装到用户级（所有项目可用）
#   bash scripts/install_skill.sh --target DIR    # 装到指定 skills 目录
#   bash scripts/install_skill.sh --force         # 覆盖已存在的同名 Skill

set -euo pipefail

SKILL_NAME="mcd-blind-box-challenge"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SOURCE="$REPO_ROOT/skill/$SKILL_NAME"

TARGET_ROOT="${HOME}/.workbuddy/skills"
FORCE=0

while [ $# -gt 0 ]; do
  case "$1" in
    --target)
      TARGET_ROOT="${2:-}"
      if [ -z "$TARGET_ROOT" ]; then
        echo "错误：--target 后面要跟一个目录" >&2
        exit 2
      fi
      shift 2
      ;;
    --force)
      FORCE=1
      shift
      ;;
    -h|--help)
      sed -n '2,12p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      echo "错误：未知参数 $1" >&2
      exit 2
      ;;
  esac
done

if [ ! -f "$SOURCE/SKILL.md" ]; then
  echo "错误：找不到 $SOURCE/SKILL.md" >&2
  echo "请在仓库根目录运行本脚本（当前解析到的仓库根：$REPO_ROOT）" >&2
  exit 2
fi

DEST="$TARGET_ROOT/$SKILL_NAME"

if [ -e "$DEST" ]; then
  if [ "$FORCE" -ne 1 ]; then
    echo "已存在：$DEST"
    echo "如果确认要覆盖，请加 --force 再跑一次。"
    exit 3
  fi
  echo "覆盖已有 Skill：$DEST"
  rm -rf "$DEST"
fi

mkdir -p "$TARGET_ROOT"
cp -R "$SOURCE" "$DEST"

echo "已安装到：$DEST"
echo
echo "目录内容："
find "$DEST" -type f | sed "s|^$DEST/|  |" | sort
echo
echo "下一步：重启对话（或新开一个对话），然后用自然语言触发，例如"
echo '  「帮我开个 30 块以内的盲盒」'
echo '  「来个不超过 600 大卡的，要辣的」'
