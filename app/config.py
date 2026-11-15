"""配置加载：config.yaml + 环境变量覆盖。"""
import os
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parent.parent


def load_config() -> dict:
    with open(_ROOT / "config.yaml", "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    volc = cfg.setdefault("volc", {})
    volc["appid"] = volc.get("appid") or os.environ.get("VOLC_APP_ID", "")
    volc["access_token"] = volc.get("access_token") or os.environ.get("VOLC_ACCESS_TOKEN", "")
    cfg["root"] = str(_ROOT)
    data_dir = Path(cfg["storage"]["data_dir"])
    if not data_dir.is_absolute():
        data_dir = _ROOT / data_dir
    data_dir.mkdir(parents=True, exist_ok=True)
    cfg["storage"]["data_dir"] = str(data_dir)
    return cfg


CFG = load_config()
