"""Skill 3 · 通知通道（stdout / smtp / webhook）。

关键设计：
- 所有凭据从 config.json 的 ${ENV} 占位符注入，代码与示例配置中没有任何真实密钥；
- 检测到占位符 / 空值时自动降级为 dry-run，保证 git clone 后无需配置也不会崩溃；
- instant_trigger=True 时，调用方在每笔成交后立即 send()，不等定时汇总。
"""
from __future__ import annotations

import smtplib
from email.message import EmailMessage
from typing import Any

_PLACEHOLDER_TOKENS = ("YOUR_", "EXAMPLE", "CHANGEME", "REPLACE_ME")


def is_placeholder(value: Any) -> bool:
    if value is None:
        return True
    s = str(value).strip()
    if not s:
        return True
    return any(tok in s.upper() for tok in _PLACEHOLDER_TOKENS)


class Notifier:
    def __init__(self, cfg: dict):
        self.cfg = cfg.get("notify", {})
        self.enabled = bool(self.cfg.get("enabled", False))
        self.channel = self.cfg.get("channel", "stdout")
        self.prefix = self.cfg.get("subject_prefix", "[CB-T0]")

    def send(self, subject: str, body: str) -> str:
        """返回发送状态字符串，便于写进复盘日志。"""
        if not self.enabled:
            return "disabled"
        tag = f"{self.prefix} {subject}"
        if self.channel == "smtp":
            return self._send_smtp(tag, body)
        if self.channel == "webhook":
            return self._send_webhook(tag, body)
        print(f"\n--- [NOTIFY stdout] {tag} ---\n{body}\n--- end ---")
        return "stdout"

    # -- stdout：默认通道，跨平台零依赖 ---------------------------------- #
    # -- smtp ------------------------------------------------------------ #
    def _send_smtp(self, subject: str, body: str) -> str:
        smtp = self.cfg.get("smtp", {})
        user, pwd, to = smtp.get("user"), smtp.get("password"), self.cfg.get("to")
        if is_placeholder(user) or is_placeholder(pwd) or is_placeholder(to):
            print(f"[DRY-RUN:smtp] 未配置有效 SMTP 凭据，跳过发送。\n主题: {subject}\n{body}")
            return "dry-run:smtp"
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = user
        msg["To"] = to
        msg.set_content(body)
        host, port = smtp.get("host", "smtp.qq.com"), int(smtp.get("port", 465))
        timeout = int(smtp.get("timeout", 10))
        try:
            if smtp.get("use_ssl", True):
                with smtplib.SMTP_SSL(host, port, timeout=timeout) as s:
                    s.login(user, pwd)
                    s.send_message(msg)
            else:
                with smtplib.SMTP(host, port, timeout=timeout) as s:
                    s.starttls()
                    s.login(user, pwd)
                    s.send_message(msg)
            return "smtp:sent"
        except Exception as exc:
            print(f"[WARN] SMTP 发送失败: {type(exc).__name__}: {exc}")
            return f"smtp:failed:{type(exc).__name__}"

    # -- webhook --------------------------------------------------------- #
    def _send_webhook(self, subject: str, body: str) -> str:
        url = self.cfg.get("webhook_url")
        if is_placeholder(url):
            print(f"[DRY-RUN:webhook] 未配置 WEBHOOK_URL，跳过发送。\n主题: {subject}\n{body}")
            return "dry-run:webhook"
        try:
            import requests  # 可选依赖
            requests.post(url, json={"subject": subject, "content": body}, timeout=8)
            return "webhook:sent"
        except Exception as exc:
            print(f"[WARN] webhook 发送失败: {type(exc).__name__}: {exc}")
            return f"webhook:failed:{type(exc).__name__}"
