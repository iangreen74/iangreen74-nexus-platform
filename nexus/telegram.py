"""
Telegram notification sender.

Bot token and chat ID are pulled from Secrets Manager (TELEGRAM_SECRET_ID)
so rotating them doesn't require a redeploy. In local mode we log the
message instead of calling Telegram so tests don't hit the network.
"""
from __future__ import annotations

import logging
from typing import Literal

from nexus.aws_client import get_secret
from nexus.config import MODE, TELEGRAM_SECRET_ID

logger = logging.getLogger("nexus.telegram")

Level = Literal["info", "warning", "critical", "healing", "resolved"]

_PREFIXES: dict[str, str] = {
    "info": "",
    "warning": "⚠️ ",
    "critical": "🚨 ",
    "healing": "🔧 ",
    "resolved": "✅ ",
}


def _format(message: str, level: Level) -> str:
    return f"{_PREFIXES.get(level, '')}{message}".strip()


def send_alert(message: str, level: Level = "info") -> bool:
    """
    Send a formatted alert to the NEXUS Telegram chat.

    Returns True on success, False if the send failed or we're in local mode.
    Failure never raises — alerting must not crash the control plane.
    """
    formatted = _format(message, level)

    if MODE != "production":
        logger.info("[local telegram/%s] %s", level, formatted)
        return False

    try:
        import requests  # noqa: WPS433 — lazy to keep local mode light

        secret = get_secret(TELEGRAM_SECRET_ID)
        # The hyperlev/slack secret stores the Telegram bot token under
        # `telegram_token` (not `telegram_bot_token`). Fall back to the
        # other shapes for forward compat.
        token = (
            secret.get("telegram_token")
            or secret.get("telegram_bot_token")
            or secret.get("bot_token")
        )
        chat_id = secret.get("telegram_chat_id") or secret.get("chat_id")
        if not token or not chat_id:
            logger.error("Telegram secret missing token/chat_id (have keys: %s)", list(secret.keys()))
            return False
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        resp = requests.post(
            url,
            json={"chat_id": chat_id, "text": formatted, "parse_mode": "Markdown"},
            timeout=10,
        )
        if resp.status_code != 200:
            logger.warning("Telegram send returned %s: %s", resp.status_code, resp.text[:200])
        return resp.status_code == 200
    except Exception:
        logger.exception("Telegram send failed for level=%s", level)
        return False
