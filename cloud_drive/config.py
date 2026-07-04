import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

_DEFAULT_CONFIG = {
    "bucket": None,
    "default_storage_class": "GLACIER_IR",
    "storage_rules": [],
    "s3_prefix": "hd-backup",
    "index_db": "~/.cloud-drive/index.db",
    "threads": 4,
    "multipart_threshold": 100 * 1024 * 1024,
    "exclude": [
        "**/.DS_Store",
        "**/Thumbs.db",
        "**/*.tmp",
        "**/.Spotlight-*",
        "**/.Trashes",
    ],
}

_CONFIG_SEARCH_PATHS = [
    Path.cwd() / "config.yaml",
    Path.home() / ".cloud-drive" / "config.yaml",
]


def load(config_path: str | None = None) -> dict:
    load_dotenv()

    cfg = dict(_DEFAULT_CONFIG)

    paths = [Path(config_path)] if config_path else _CONFIG_SEARCH_PATHS
    for p in paths:
        if p.exists():
            with open(p) as f:
                overrides = yaml.safe_load(f) or {}
            cfg.update(overrides)
            break

    cfg["index_db"] = str(Path(cfg["index_db"]).expanduser())
    return cfg


def storage_class_for(cfg: dict, relative_path: str) -> str:
    for rule in cfg.get("storage_rules", []):
        if relative_path.startswith(rule["prefix"]):
            return rule["storage_class"]
    return cfg["default_storage_class"]
