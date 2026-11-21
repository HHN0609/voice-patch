"""配置加载：config.yaml + .env + 系统环境变量。

优先级：系统环境变量 > .env > config.yaml。.env 不覆盖已存在的环境变量。
"""
import os
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv() -> None:
    env_file = _ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv()


def load_config() -> dict:
    with open(_ROOT / "config.yaml", "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    volc = cfg.setdefault("volc", {})
    volc["api_key"] = volc.get("api_key") or os.environ.get("VOLC_API_KEY", "")
    volc["appid"] = volc.get("appid") or os.environ.get("VOLC_APP_ID", "")
    volc["access_token"] = volc.get("access_token") or os.environ.get("VOLC_ACCESS_TOKEN", "")
    pan = cfg.setdefault("pan", {"base_url": "https://pan.ezv.cc"})
    pan["api_key"] = pan.get("api_key") or os.environ.get("PAN_API_KEY", "")
    ds = cfg.setdefault("deepseek", {})
    ds["api_key"] = ds.get("api_key") or os.environ.get("DEEPSEEK_API_KEY", "")
    # LLM 编排顾问默认复用 DeepSeek Key
    llm = cfg.setdefault("llm", {})
    llm["api_key"] = llm.get("api_key") or ds["api_key"]
    cfg["root"] = str(_ROOT)
    data_dir = Path(cfg["storage"]["data_dir"])
    if not data_dir.is_absolute():
        data_dir = _ROOT / data_dir
    data_dir.mkdir(parents=True, exist_ok=True)
    cfg["storage"]["data_dir"] = str(data_dir)
    return cfg


CFG = load_config()
