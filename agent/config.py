"""Constants and configuration loading."""

import json
import logging
from pathlib import Path

_CONTAINER_PLUGIN = Path("/plugins/oape")
_CONTAINER_CONFIG = Path("/config/config.json")
_RELATIVE_PLUGIN = Path(__file__).resolve().parent.parent / "plugins" / "oape"
_RELATIVE_CONFIG = Path(__file__).resolve().parent.parent / "config" / "config.json"

PLUGIN_DIR = str(_CONTAINER_PLUGIN if _CONTAINER_PLUGIN.exists() else _RELATIVE_PLUGIN)

CONVERSATION_LOG = Path("/tmp/conversation.log")

conv_logger = logging.getLogger("conversation")
conv_logger.setLevel(logging.INFO)
_handler = logging.FileHandler(CONVERSATION_LOG)
_handler.setFormatter(logging.Formatter("%(message)s"))
conv_logger.addHandler(_handler)


def load_config() -> dict:
    """Load config.json from the mounted ConfigMap (or local fallback)."""
    config_path = _CONTAINER_CONFIG if _CONTAINER_CONFIG.exists() else _RELATIVE_CONFIG
    with open(config_path) as cf:
        return json.loads(cf.read())
