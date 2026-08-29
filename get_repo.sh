#!/bin/bash

# 定义 Defects4J 的项目列表
# 你可以先用一两个项目测试: PROJECTS=("Lang" "Chart")
PROJECTS=("Cli" "Codec" "Collections" "Compress" "Csv" "Gson" "JacksonCore" "JacksonDatabind" "JacksonXml" "Jsoup" "JxPath" "Lang" "Math" "Time")

# 所有路径都可以通过环境变量覆盖。
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BASE_BUGGY_DIR="${DP_CFG_BUGGY_ROOT:-$SCRIPT_DIR/data/project_under_test}"
BASE_FIXED_DIR="${DP_CFG_FIXED_ROOT:-$SCRIPT_DIR/data/project_fixed}"
DEFECTS4J_BIN="${DEFECTS4J_BIN:-defects4j}"

# 确保基础目录存在
mkdir -p "$BASE_BUGGY_DIR"
mkdir -p "$BASE_FIXED_DIR"

# 遍历所有项目
for project in "${PROJECTS[@]}"; do
  echo "=================================================="
  echo "Processing project: $project"
  echo "=================================================="

  # 获取该项目的所有bug ID列表
  bug_ids=$("$DEFECTS4J_BIN" query -p "$project" -q "bug.id")

  # 遍历该项目的所有bug
  for bug_id in $bug_ids; do
    echo "  -> Checking out $project-$bug_id..."

    # 定义缺陷版本和修复版本的ID
    buggy_version_id="${bug_id}b"
    fixed_version_id="${bug_id}f"

    # 定义检出的目标目录
    buggy_dir="${BASE_BUGGY_DIR}/${project}/${project}_${bug_id}_buggy"
    fixed_dir="${BASE_FIXED_DIR}/${project}/${project}_${bug_id}_fixed"

    # 检出缺陷版本
    echo "     - Buggy version to $buggy_dir"
    "$DEFECTS4J_BIN" checkout -p "$project" -v "$buggy_version_id" -w "$buggy_dir"

    # 检出修复版本
    echo "     - Fixed version to $fixed_dir"
    "$DEFECTS4J_BIN" checkout -p "$project" -v "$fixed_version_id" -w "$fixed_dir"

    echo "  -> Done with $project-$bug_id."
    echo ""
  done
done

echo "All checkouts are complete!"
