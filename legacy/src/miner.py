"""Points miner orchestration."""

import os
import threading
import time

from loguru import logger

from . import web_server
from .client import KickClient
from .config import Config
from .monitor import ChannelMonitor


class PointsMiner:
    """Orchestrates mining points across multiple channels."""

    def __init__(self, config: Config):
        self.config = config
        self.client = KickClient(config.authorization)
        self.monitors: list[ChannelMonitor] = []

    def start(self) -> None:
        """Start monitoring all configured channels."""
        threads = []

        for username in self.config.channels:
            monitor = ChannelMonitor(
                username=username,
                client=self.client,
                messages=self.config.messages,
                wait_times=self.config.wait_times,
                prediction_amount=(self.config.prediction or {}).get("amount", 50)
                if (self.config.prediction or {}).get("enabled", True)
                else 0,
            )
            self.monitors.append(monitor)

            thread = threading.Thread(target=monitor.run, daemon=True)
            threads.append(thread)
            thread.start()

        logger.success(f"Started monitoring {len(self.config.channels)} channels")

        # Start web dashboard (optional, configured in config.json)
        web_cfg = self.config.web_dashboard or {}
        if web_cfg.get("enabled", False):
            port = int(os.environ.get("PORT", web_cfg.get("port", 4000)))
            web_server.set_active()
            web_server.start_server(port=port)

        # Start Telegram bot (optional)
        tg_cfg = self.config.telegram or {}
        tg_token = os.environ.get("TELEGRAM_BOT_TOKEN") or tg_cfg.get("bot_token", "")
        if tg_token:
            from .telegram_bot import start_bot, set_miner

            set_miner(self)
            allowed = tg_cfg.get("allowed_user_ids") or []
            start_bot(token=tg_token, allowed_users=allowed or None)
        else:
            logger.info("Telegram bot not configured (no token). Skipping.")

        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("Shutting down...")
