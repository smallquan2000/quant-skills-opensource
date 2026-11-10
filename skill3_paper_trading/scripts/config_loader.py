"""轻量配置加载器（零第三方依赖，仅标准库）。

设计目标（跨平台关键）：
1. 所有路径相对「本 Skill 根目录」解析，与调用时的 cwd 无关；
2. 绝不硬编码任何绝对路径（例如 /workspace/cb_t0）；
3. 支持 ${ENV_VAR} 占位符，从 .env / 环境变量注入密钥，真实密钥不进代码；
4. config.json 缺失时自动回落 config.example.json，git clone 后免配置即可演示。

本文件在 4 个 Skill 中各保留一份相同副本 —— 刻意如此，以保证每个 Skill
可以被单独下载 / 单独 git clone 子目录后独立运行，互不依赖。
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

SKILL_ROOT = Path(__file__).resolve().parent.parent
_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-[^}]*)?\}")


def _load_dotenv(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    if not path.exists():
        return result
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        result[key.strip()] = value.strip().strip('"').strip("'")
    return result


def _expand(value: Any, env: dict[str, str]) -> Any:
    if isinstance(value, str):
        def repl(match: re.Match[str]) -> str:
            token = match.group(1)
            inner = match.group(0)[2:-1]
            default = inner.split(":-", 1)[1] if ":-" in inner else ""
            return env.get(token) or os.environ.get(token) or default
        return _ENV_PATTERN.sub(repl, value)
    if isinstance(value, dict):
        return {k: _expand(v, env) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand(v, env) for v in value]
    return value


def load_config(config_path: str | None = None, env_file: str | None = None) -> dict:
    cfg_file = Path(config_path).expanduser() if config_path else SKILL_ROOT / "config.json"
    if not cfg_file.is_absolute():
        cfg_file = (SKILL_ROOT / cfg_file).resolve()
    if not cfg_file.exists():
        cfg_file = SKILL_ROOT / "config.example.json"

    env_path = Path(env_file).expanduser() if env_file else SKILL_ROOT / ".env"
    if not env_path.is_absolute():
        env_path = (SKILL_ROOT / env_path).resolve()

    env = {
        **_load_dotenv(SKILL_ROOT / ".env.example"),
        **_load_dotenv(env_path),
    }
    raw = json.loads(cfg_file.read_text(encoding="utf-8"))
    return _expand(raw, env)


def resolve(path_value: str, base: Path | None = None) -> Path:
    path = Path(path_value).expanduser()
    if not path.is_absolute():
        path = (base or SKILL_ROOT) / path
    return path.resolve()


def project_root() -> Path:
    return SKILL_ROOT.parent


def ensure_parent(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    return path
