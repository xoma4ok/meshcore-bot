"""Tests for modules/service_plugins/tg_alert_service.py — TgAlertService."""

import asyncio
from configparser import ConfigParser
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from modules.service_plugins.tg_alert_service import TgAlertService


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_bot(mock_logger, overrides=None):
    """Return a minimal mock bot with a valid [Tg_Alert_Service] config."""
    bot = MagicMock()
    bot.logger = mock_logger
    bot.config = ConfigParser()
    bot.config.add_section("Tg_Alert_Service")
    defaults = {
        "enabled": "true",
        "tg_alert_service_api_id": "12345",
        "tg_alert_service_api_hash": "abc123hash",
        "tg_alert_service_phone": "+15550001234",
        "tg_alert_service_session_name": "test_session",
        "tg_alert_service_channels": "@chan1, @chan2",
        "tg_alert_service_meshcore_channel": "#warning",
        "tg_alert_service_patterns": "alert|warning, emergency",
        "tg_alert_service_max_chunk_len": "60",
        "tg_alert_service_chunk_delay": "0",
    }
    if overrides:
        defaults.update(overrides)
    for k, v in defaults.items():
        bot.config.set("Tg_Alert_Service", k, v)
    bot.command_manager = MagicMock()
    bot.command_manager.send_channel_message = AsyncMock(return_value=True)
    return bot


@pytest.fixture
def bot(mock_logger):
    with patch("modules.service_plugins.tg_alert_service.TELETHON_AVAILABLE", True):
        yield _make_bot(mock_logger)


def _make_service(mock_logger, overrides=None):
    bot = _make_bot(mock_logger, overrides)
    with patch("modules.service_plugins.tg_alert_service.TELETHON_AVAILABLE", True):
        svc = TgAlertService(bot)
    return svc, bot


# ---------------------------------------------------------------------------
# TestInit — config loading and validation
# ---------------------------------------------------------------------------

class TestInit:
    def test_enabled_with_valid_config(self, mock_logger):
        svc, _ = _make_service(mock_logger)
        assert svc.enabled is True

    def test_disabled_when_telethon_missing(self, mock_logger):
        bot = _make_bot(mock_logger)
        with patch("modules.service_plugins.tg_alert_service.TELETHON_AVAILABLE", False):
            svc = TgAlertService(bot)
        assert svc.enabled is False

    def test_disabled_when_api_id_missing(self, mock_logger):
        svc, _ = _make_service(mock_logger, {"tg_alert_service_api_id": "0"})
        assert svc.enabled is False

    def test_disabled_when_api_hash_missing(self, mock_logger):
        svc, _ = _make_service(mock_logger, {"tg_alert_service_api_hash": ""})
        assert svc.enabled is False

    def test_disabled_when_channels_missing(self, mock_logger):
        svc, _ = _make_service(mock_logger, {"tg_alert_service_channels": ""})
        assert svc.enabled is False

    def test_disabled_when_meshcore_channel_missing(self, mock_logger):
        svc, _ = _make_service(mock_logger, {"tg_alert_service_meshcore_channel": ""})
        assert svc.enabled is False

    def test_disabled_when_patterns_missing(self, mock_logger):
        svc, _ = _make_service(mock_logger, {"tg_alert_service_patterns": ""})
        assert svc.enabled is False

    def test_disabled_when_invalid_regex(self, mock_logger):
        svc, _ = _make_service(mock_logger, {"tg_alert_service_patterns": "["})
        assert svc.enabled is False

    def test_channels_parsed(self, mock_logger):
        svc, _ = _make_service(mock_logger)
        assert svc._channels == ["@chan1", "@chan2"]

    def test_meshcore_channel_loaded(self, mock_logger):
        svc, _ = _make_service(mock_logger)
        assert svc._meshcore_channel == "#warning"

    def test_max_chunk_len_loaded(self, mock_logger):
        svc, _ = _make_service(mock_logger)
        assert svc._max_chunk_len == 60

    def test_chunk_delay_loaded(self, mock_logger):
        svc, _ = _make_service(mock_logger)
        assert svc._chunk_delay == 0.0

    def test_session_name_loaded(self, mock_logger):
        svc, _ = _make_service(mock_logger)
        assert svc._session_name == "test_session"

    def test_phone_loaded(self, mock_logger):
        svc, _ = _make_service(mock_logger)
        assert svc._phone == "+15550001234"

    def test_default_chunk_len_when_not_set(self, mock_logger):
        svc, _ = _make_service(mock_logger, {"tg_alert_service_max_chunk_len": "120"})
        assert svc._max_chunk_len == 120

    def test_default_session_name_when_not_set(self, mock_logger):
        bot = _make_bot(mock_logger)
        bot.config.remove_option("Tg_Alert_Service", "tg_alert_service_session_name")
        with patch("modules.service_plugins.tg_alert_service.TELETHON_AVAILABLE", True):
            svc = TgAlertService(bot)
        assert svc._session_name == "tg_alert_session"


# ---------------------------------------------------------------------------
# TestSplitText — pure logic, no mocks needed
# ---------------------------------------------------------------------------

class TestSplitText:
    def test_short_text_not_split(self):
        assert TgAlertService._split_text("hello world", 60) == ["hello world"]

    def test_exact_length_not_split(self):
        text = "a" * 60
        assert TgAlertService._split_text(text, 60) == [text]

    def test_splits_on_word_boundary(self):
        text = "one two three four five"
        chunks = TgAlertService._split_text(text, 12)
        for chunk in chunks:
            assert len(chunk) <= 12
        assert " ".join(chunks) == text

    def test_no_word_broken(self):
        # "short" fits in first chunk; the long word is hard-split but never cut mid-char
        text = "short " + "x" * 70
        chunks = TgAlertService._split_text(text, 60)
        assert chunks[0] == "short"
        assert all(len(c) <= 60 for c in chunks)

    def test_hard_split_when_no_space(self):
        text = "a" * 10
        chunks = TgAlertService._split_text(text, 4)
        for chunk in chunks:
            assert len(chunk) <= 4

    def test_empty_string(self):
        assert TgAlertService._split_text("", 60) == [""]

    def test_max_len_one(self):
        chunks = TgAlertService._split_text("abc", 1)
        assert all(len(c) == 1 for c in chunks)
        assert "".join(chunks) == "abc"

    def test_long_message_correct_chunk_count(self):
        text = " ".join(["word"] * 50)  # 249 chars
        chunks = TgAlertService._split_text(text, 60)
        assert all(len(c) <= 60 for c in chunks)
        assert len(chunks) > 1


# ---------------------------------------------------------------------------
# TestPatternMatching — compiled regex behaviour
# ---------------------------------------------------------------------------

class TestPatternMatching:
    def test_single_pattern_matches(self, mock_logger):
        svc, _ = _make_service(mock_logger, {"tg_alert_service_patterns": "alert"})
        assert svc._compiled_pattern.search("This is an ALERT message")

    def test_single_pattern_no_match(self, mock_logger):
        svc, _ = _make_service(mock_logger, {"tg_alert_service_patterns": "alert"})
        assert not svc._compiled_pattern.search("Normal message")

    def test_multiple_patterns_or_logic_first(self, mock_logger):
        svc, _ = _make_service(mock_logger, {"tg_alert_service_patterns": "alert, emergency"})
        assert svc._compiled_pattern.search("alert issued")

    def test_multiple_patterns_or_logic_second(self, mock_logger):
        svc, _ = _make_service(mock_logger, {"tg_alert_service_patterns": "alert, emergency"})
        assert svc._compiled_pattern.search("EMERGENCY declared")

    def test_multiple_patterns_no_match(self, mock_logger):
        svc, _ = _make_service(mock_logger, {"tg_alert_service_patterns": "alert, emergency"})
        assert not svc._compiled_pattern.search("weather update")

    def test_inline_or_pattern(self, mock_logger):
        svc, _ = _make_service(mock_logger, {"tg_alert_service_patterns": "fire|flood"})
        assert svc._compiled_pattern.search("flood warning")
        assert svc._compiled_pattern.search("fire hazard")

    def test_case_insensitive(self, mock_logger):
        svc, _ = _make_service(mock_logger, {"tg_alert_service_patterns": "warning"})
        assert svc._compiled_pattern.search("WARNING: road closed")


# ---------------------------------------------------------------------------
# TestRelayToMeshcore — send logic and chunking
# ---------------------------------------------------------------------------

class TestRelayToMeshcore:
    async def test_short_message_sends_single_chunk(self, mock_logger):
        svc, bot = _make_service(mock_logger)
        await svc._relay_to_meshcore("Short alert")
        bot.command_manager.send_channel_message.assert_awaited_once()
        args = bot.command_manager.send_channel_message.call_args[0]
        assert args[0] == "#warning"
        assert args[1] == "Short alert"

    async def test_long_message_sends_multiple_chunks(self, mock_logger):
        svc, bot = _make_service(mock_logger, {"tg_alert_service_max_chunk_len": "20"})
        text = "one two three four five six seven eight"
        await svc._relay_to_meshcore(text)
        assert bot.command_manager.send_channel_message.await_count > 1

    async def test_all_chunks_sent_to_correct_channel(self, mock_logger):
        svc, bot = _make_service(mock_logger, {"tg_alert_service_max_chunk_len": "20"})
        text = "one two three four five six seven eight"
        await svc._relay_to_meshcore(text)
        for call in bot.command_manager.send_channel_message.call_args_list:
            assert call[0][0] == "#warning"

    async def test_send_exception_does_not_propagate(self, mock_logger):
        svc, bot = _make_service(mock_logger)
        bot.command_manager.send_channel_message = AsyncMock(side_effect=RuntimeError("mesh down"))
        # Should not raise
        await svc._relay_to_meshcore("alert message")

    async def test_chunk_delay_applied_between_chunks(self, mock_logger):
        svc, bot = _make_service(mock_logger, {
            "tg_alert_service_max_chunk_len": "10",
            "tg_alert_service_chunk_delay": "1.5",
        })
        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            await svc._relay_to_meshcore("one two three four five")
        # sleep called once per extra chunk (not before first)
        assert mock_sleep.await_count == bot.command_manager.send_channel_message.await_count - 1

    async def test_no_delay_for_single_chunk(self, mock_logger):
        svc, bot = _make_service(mock_logger, {"tg_alert_service_chunk_delay": "5"})
        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            await svc._relay_to_meshcore("short")
        mock_sleep.assert_not_awaited()

    async def test_skip_user_rate_limit_true(self, mock_logger):
        svc, bot = _make_service(mock_logger)
        await svc._relay_to_meshcore("alert")
        _, kwargs = bot.command_manager.send_channel_message.call_args
        assert kwargs.get("skip_user_rate_limit") is True


# ---------------------------------------------------------------------------
# TestStartStop — lifecycle
# ---------------------------------------------------------------------------

class TestStartStop:
    async def test_start_sets_running_and_creates_task(self, mock_logger):
        svc, _ = _make_service(mock_logger)
        with patch.object(svc, "_run", new_callable=AsyncMock):
            await svc.start()
        assert svc._running is True
        assert svc._task is not None
        svc._task.cancel()

    async def test_start_noop_when_disabled(self, mock_logger):
        svc, _ = _make_service(mock_logger)
        svc.enabled = False
        await svc.start()
        assert svc._task is None

    async def test_stop_cancels_task(self, mock_logger):
        svc, _ = _make_service(mock_logger)
        # Create a real task that will wait until cancelled
        svc._task = asyncio.create_task(asyncio.sleep(999))
        svc._client = None
        await svc.stop()
        assert svc._task.cancelled()

    async def test_stop_disconnects_client(self, mock_logger):
        svc, _ = _make_service(mock_logger)
        mock_client = MagicMock()
        mock_client.is_connected.return_value = True
        mock_client.disconnect = AsyncMock()
        svc._client = mock_client
        svc._task = None
        await svc.stop()
        mock_client.disconnect.assert_awaited_once()

    async def test_stop_skips_disconnect_when_not_connected(self, mock_logger):
        svc, _ = _make_service(mock_logger)
        mock_client = MagicMock()
        mock_client.is_connected.return_value = False
        mock_client.disconnect = AsyncMock()
        svc._client = mock_client
        svc._task = None
        await svc.stop()
        mock_client.disconnect.assert_not_awaited()

    async def test_stop_sets_running_false(self, mock_logger):
        svc, _ = _make_service(mock_logger)
        svc._running = True
        svc._client = None
        svc._task = None
        await svc.stop()
        assert svc._running is False


# ---------------------------------------------------------------------------
# TestRun — reconnect loop
# ---------------------------------------------------------------------------

class TestRun:
    async def test_run_calls_connect_and_listen(self, mock_logger):
        svc, _ = _make_service(mock_logger)
        call_count = 0

        async def fake_connect():
            nonlocal call_count
            call_count += 1
            svc._running = False  # stop after first call

        svc._running = True
        with patch.object(svc, "_connect_and_listen", side_effect=fake_connect):
            await svc._run()
        assert call_count == 1

    async def test_run_reconnects_after_error(self, mock_logger):
        svc, _ = _make_service(mock_logger)
        call_count = 0

        async def fake_connect():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("connection failed")
            svc._running = False

        svc._running = True
        with patch.object(svc, "_connect_and_listen", side_effect=fake_connect), \
             patch("asyncio.sleep", new_callable=AsyncMock):
            await svc._run()
        assert call_count == 2

    async def test_run_stops_on_cancelled_error(self, mock_logger):
        svc, _ = _make_service(mock_logger)
        svc._running = True
        call_count = 0

        async def raise_cancelled():
            nonlocal call_count
            call_count += 1
            raise asyncio.CancelledError

        # _run catches CancelledError with `break`, so it should return normally
        with patch.object(svc, "_connect_and_listen", side_effect=raise_cancelled):
            await asyncio.wait_for(svc._run(), timeout=2.0)

        assert call_count == 1  # did not retry after CancelledError
