#!/usr/bin/env bash
# 轻量级守护 —— 【仅方案 A：VPS / 本地服务器】使用。
#
# ⚠️ 云沙箱环境不要用本脚本，请改用 scripts/embedded_scheduler.py（方案 B）。
# 原因（见 docs/ENVIRONMENT_NOTES.md §1）：本脚本自身依赖 cron 每分钟调用，
# 而云沙箱休眠会直接杀掉 cron —— 守护者与被守护者处在同一故障域，形成逻辑死锁，
# cron 一死就没人再调用本脚本，「自我拉起」根本不会发生。
#
# 在 VPS / 本地服务器上（cron 不会被杀）本脚本仍然有效：每分钟检查并拉起 cron
# 与可选业务进程，跑一次 healthcheck，并滚动日志。
#
# 本脚本不硬编码任何绝对路径：仓库位置由脚本自身定位，解释器 / 重启命令由环境变量注入。
# crontab 调用示例（请把 <...> 替换为你自己的实际值，不要提交真实路径）：
#   * * * * * REPO_DIR=<REPO_DIR> PYTHON_BIN=<PYTHON_BIN> bash <REPO_DIR>/scripts/keepalive.sh
#
set -u

REPO_DIR="${REPO_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
LOG_DIR="${LOG_DIR:-$REPO_DIR/data}"
LOG_FILE="${LOG_FILE:-$LOG_DIR/keepalive.log}"
mkdir -p "$LOG_DIR"

log() { printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$1" >> "$LOG_FILE"; }

# 1) cron / crond 存活检查与拉起
if ! pgrep -x cron >/dev/null 2>&1 && ! pgrep -x crond >/dev/null 2>&1; then
  log "[WARN] cron 未运行，尝试拉起"
  if command -v service >/dev/null 2>&1; then
    service cron start >>"$LOG_FILE" 2>&1 || service crond start >>"$LOG_FILE" 2>&1 || \
      log "[ERROR] service 拉起失败"
  elif command -v cron >/dev/null 2>&1; then
    cron >>"$LOG_FILE" 2>&1 || log "[ERROR] cron 拉起失败"
  else
    log "[ERROR] 未找到 cron 可执行文件，请检查环境"
  fi
fi

# 2) 可选：业务进程（如 Flask 看板）存活检查与拉起
#    PROCESS_PATTERN 用于 pgrep 匹配，RESTART_CMD 为拉起命令，均从环境注入。
if [ -n "${PROCESS_PATTERN:-}" ] && ! pgrep -f "$PROCESS_PATTERN" >/dev/null 2>&1; then
  log "[WARN] 业务进程未运行: $PROCESS_PATTERN"
  if [ -n "${RESTART_CMD:-}" ]; then
    (cd "$REPO_DIR" && bash -c "$RESTART_CMD") >>"$LOG_FILE" 2>&1 || \
      log "[ERROR] 业务进程拉起失败"
  else
    log "[WARN] 未配置 RESTART_CMD，仅记录不拉起"
  fi
fi

# 3) 可选：执行一次自检并把结论写入日志（不因 WARN 中断）
if [ -f "$REPO_DIR/scripts/healthcheck.py" ]; then
  "$PYTHON_BIN" "$REPO_DIR/scripts/healthcheck.py" --max-age-hours 48 --json \
    >>"$LOG_FILE" 2>&1 || log "[WARN] healthcheck 返回非零"
fi

# 4) 日志滚动：只保留最近 500 行，避免长期运行撑爆磁盘
if [ -f "$LOG_FILE" ] && [ "$(wc -l <"$LOG_FILE")" -gt 500 ]; then
  tail -n 500 "$LOG_FILE" > "$LOG_FILE.tmp" && mv "$LOG_FILE.tmp" "$LOG_FILE"
fi
