#!/usr/bin/env bash
# 一键跑通 4 个 Skill 的完整链路（全部离线，无需任何密钥）。
# 从仓库根目录执行：bash scripts/run_all.sh
# 所有路径都相对于脚本自身定位，可在任意目录调用。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"

echo "=== [1/4] 数据管道：采集 + 归一化 + 落库 ==="
(cd "$ROOT/skill1_data_pipeline" && \
  "$PYTHON_BIN" scripts/fetch_cb_data.py --provider csv --export-csv data/cb_snapshot.csv)

echo ""
echo "=== [2/4] 打分引擎：五维打分 → Top3 信号 ==="
(cd "$ROOT/skill2_scoring_engine" && \
  "$PYTHON_BIN" scripts/score.py --input ../skill1_data_pipeline/data/cb_snapshot.csv)

echo ""
echo "=== [3/4] 模拟盘：建仓 → 复盘 ==="
(cd "$ROOT/skill3_paper_trading" && \
  "$PYTHON_BIN" scripts/paper_engine.py --mode reset && \
  "$PYTHON_BIN" scripts/paper_engine.py --mode buy && \
  "$PYTHON_BIN" scripts/paper_engine.py --mode review)

echo ""
echo "=== [4/4] 离线回测：生成演示数据 → 推演 ==="
(cd "$ROOT/skill4_backtest" && \
  "$PYTHON_BIN" scripts/make_sample_data.py && \
  "$PYTHON_BIN" scripts/backtest.py)

echo ""
echo "=== [自检] ==="
"$PYTHON_BIN" "$ROOT/scripts/healthcheck.py" || true

echo ""
echo "✅ 全链路完成。产物位于各 Skill 的 data/ 目录。"
