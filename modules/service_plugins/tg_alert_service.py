#!/usr/bin/env python3
"""
Telegram Alert Service for MeshCore Bot
Monitors Telegram channels and relays matching messages to MeshCore mesh channels.
"""

import asyncio
import contextlib
import re
from typing import Any, Optional

from .base_service import BaseServicePlugin

try:
    from telethon import TelegramClient, events
    from telethon.errors import FloodWaitError
    TELETHON_AVAILABLE = True
except ImportError:
    TELETHON_AVAILABLE = False


class TgAlertService(BaseServicePlugin):
    """Monitors configured Telegram channels and forwards matching messages to MeshCore.

    WHY USER-ACCOUNT API (not bot token):
        Telegram bots can only receive messages from channels where they are added
        as an administrator. Reading public channels without admin rights requires a
        regular Telegram user account via the MTProto API (Telethon library).
        A bot token is insufficient for this use case.

    FIRST-TIME SETUP — three steps:
        1. Get api_id and api_hash:
           - Go to https://my.telegram.org/apps
           - Log in with your Telegram account (phone + OTP)
           - Create an application (any name/platform)
           - Copy "App api_id" (integer) and "App api_hash" (hex string)

        2. Set tg_alert_service_phone to your Telegram account phone number
           in international format, e.g. +15551234567

        3. Run the bot once. Telethon will prompt for the OTP sent to your
           Telegram app. After successful login a session file is saved
           (tg_alert_session.session by default). On all subsequent runs
           the session file is reused — no phone or OTP needed again.
           You may then clear tg_alert_service_phone from the config.

    Config section: [Tg_Alert_Service]

    Required:
        tg_alert_service_api_id           — Telegram app API ID (integer, from my.telegram.org)
        tg_alert_service_api_hash         — Telegram app API hash (from my.telegram.org)
        tg_alert_service_phone            — phone number for first-time auth (international format)
        tg_alert_service_channels         — comma-separated channel usernames/IDs to monitor
        tg_alert_service_meshcore_channel — MeshCore channel name (e.g. #warning)
        tg_alert_service_patterns         — comma-separated regex patterns (OR logic)

    Optional:
        tg_alert_service_session_name  — Telethon session file name (default: tg_alert_session)
        tg_alert_service_max_chunk_len — max chars per MeshCore message chunk (default: 120)
        tg_alert_service_chunk_delay   — seconds between chunks (default: 2)
    """

    config_section = "Tg_Alert_Service"
    description = "Monitors Telegram channels and relays matching messages to MeshCore"

    def __init__(self, bot: Any) -> None:
        super().__init__(bot)

        if not TELETHON_AVAILABLE:
            self.logger.error(
                "telethon is not installed. Install it with: pip install telethon"
            )
            self.enabled = False
            return

        cfg = self.bot.config
        section = self.config_section

        self._api_id: int = cfg.getint(section, "tg_alert_service_api_id", fallback=0)
        self._api_hash: str = cfg.get(section, "tg_alert_service_api_hash", fallback="").strip()
        self._session_name: str = cfg.get(
            section, "tg_alert_service_session_name", fallback="tg_alert_session"
        ).strip()
        self._phone: str = cfg.get(section, "tg_alert_service_phone", fallback="").strip()

        channels_raw = cfg.get(section, "tg_alert_service_channels", fallback="").strip()
        self._channels: list[str] = [
            c.strip() for c in channels_raw.split(",") if c.strip()
        ]

        self._meshcore_channel: str = cfg.get(
            section, "tg_alert_service_meshcore_channel", fallback=""
        ).strip()

        patterns_raw = cfg.get(section, "tg_alert_service_patterns", fallback="").strip()
        raw_patterns = [p.strip() for p in patterns_raw.split(",") if p.strip()]
        self._compiled_pattern: Optional[re.Pattern] = None
        if raw_patterns:
            combined = "|".join(f"(?:{p})" for p in raw_patterns)
            try:
                self._compiled_pattern = re.compile(combined, re.IGNORECASE)
            except re.error as exc:
                self.logger.error("tg_alert_service: invalid regex pattern(s): %s", exc)
                self.enabled = False
                return

        self._max_chunk_len: int = cfg.getint(
            section, "tg_alert_service_max_chunk_len", fallback=120
        )
        self._chunk_delay: float = cfg.getfloat(
            section, "tg_alert_service_chunk_delay", fallback=2.0
        )

        if not self._api_id or not self._api_hash:
            self.logger.error(
                "tg_alert_service: tg_alert_service_api_id and tg_alert_service_api_hash are required"
            )
            self.enabled = False
            return

        if not self._channels:
            self.logger.error("tg_alert_service: tg_alert_service_channels is required")
            self.enabled = False
            return

        if not self._meshcore_channel:
            self.logger.error("tg_alert_service: tg_alert_service_meshcore_channel is required")
            self.enabled = False
            return

        if not self._compiled_pattern:
            self.logger.error("tg_alert_service: tg_alert_service_patterns is required")
            self.enabled = False
            return

        self._client: Optional[Any] = None  # TelegramClient
        self._task: Optional[asyncio.Task] = None

        self.logger.info(
            "TgAlertService initialized: channels=%s, meshcore_channel=%s, "
            "max_chunk_len=%d, chunk_delay=%.1fs",
            self._channels,
            self._meshcore_channel,
            self._max_chunk_len,
            self._chunk_delay,
        )

    async def start(self) -> None:
        if not self.enabled:
            return
        self._running = True
        self._task = asyncio.create_task(self._run())
        self.logger.info("TgAlertService started")

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        if self._client and self._client.is_connected():
            await self._client.disconnect()
            self.logger.info("TgAlertService: Telegram client disconnected")
        self.logger.info("TgAlertService stopped")

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _run(self) -> None:
        """Main loop: connect Telethon client and listen for new messages."""
        while self._running:
            try:
                await self._connect_and_listen()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                self.logger.error("TgAlertService error: %s — reconnecting in 30s", exc)
                await asyncio.sleep(30)

    async def _connect_and_listen(self) -> None:
        client = TelegramClient(self._session_name, self._api_id, self._api_hash)
        self._client = client

        await client.start(phone=self._phone if self._phone else None)
        self.logger.info("TgAlertService: connected to Telegram")

        # Resolve channel entities once so we can filter by chat
        resolved: list[Any] = []
        for ch in self._channels:
            try:
                entity = await client.get_entity(ch)
                resolved.append(entity)
                self.logger.info("TgAlertService: subscribed to channel %s (id=%s)", ch, entity.id)
            except Exception as exc:
                self.logger.warning("TgAlertService: cannot resolve channel %r: %s", ch, exc)

        if not resolved:
            self.logger.error("TgAlertService: no channels resolved, aborting")
            return

        @client.on(events.NewMessage(chats=resolved))
        async def handler(event: Any) -> None:
            text = event.message.message or ""
            if not text:
                return
            if not self._compiled_pattern or not self._compiled_pattern.search(text):
                return
            self.logger.info(
                "TgAlertService: match in chat %s: %s…", event.chat_id, text[:60]
            )
            await self._relay_to_meshcore(text)

        try:
            await client.run_until_disconnected()
        except FloodWaitError as exc:
            self.logger.warning("TgAlertService: FloodWait %ds", exc.seconds)
            await asyncio.sleep(exc.seconds)
        finally:
            if client.is_connected():
                await client.disconnect()

    async def _relay_to_meshcore(self, text: str) -> None:
        """Split text into chunks and send to the configured MeshCore channel."""
        chunks = self._split_text(text, self._max_chunk_len)
        for i, chunk in enumerate(chunks):
            if i > 0 and self._chunk_delay > 0:
                await asyncio.sleep(self._chunk_delay)
            try:
                await self.bot.command_manager.send_channel_message(
                    self._meshcore_channel,
                    chunk,
                    skip_user_rate_limit=True,
                    scope=self.get_mesh_flood_scope(),
                )
            except Exception as exc:
                self.logger.error("TgAlertService: failed to send chunk %d: %s", i + 1, exc)

    @staticmethod
    def _split_text(text: str, max_len: int) -> list[str]:
        """Split text into chunks of at most max_len chars, breaking on word boundaries."""
        if max_len < 1:
            max_len = 1
        if len(text) <= max_len:
            return [text]
        chunks: list[str] = []
        while text:
            if len(text) <= max_len:
                chunks.append(text)
                break
            split_at = text.rfind(" ", 0, max_len + 1)
            if split_at <= 0:
                split_at = max_len
            chunks.append(text[:split_at].rstrip())
            text = text[split_at:].lstrip()
        return chunks
