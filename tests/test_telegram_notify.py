#!/usr/bin/env python3
"""Telegram structured-only notifications."""

import unittest
from unittest.mock import patch

from core.ai_notifier import TelegramAIComposer, sanitize_telegram_message
from core.council_budget import telegram_structured_only
from core.config import BotConfig


class TestSanitizeTelegram(unittest.TestCase):
    def test_rejects_prompt_leak(self):
        self.assertEqual(sanitize_telegram_message("• Never canned templates"), "")
        self.assertEqual(sanitize_telegram_message('_pnl": 0.0, "cost_basis": 1'), "")
        self.assertEqual(
            sanitize_telegram_message("• Never invent (auto-agree with commander)"), ""
        )

    def test_keeps_structured_trade(self):
        msg = "✅ FLIGHT CLOSED │ INTC · LOSS\nP&L $-7.24"
        self.assertEqual(sanitize_telegram_message(msg), msg)


class TestStructuredCompose(unittest.TestCase):
    @patch.dict("os.environ", {"TELEGRAM_STRUCTURED_ONLY": "true"}, clear=False)
    def test_trade_closed_structured(self):
        cfg = BotConfig()
        comp = TelegramAIComposer(cfg)
        out = comp.compose(
            "trade_closed",
            {"ticker": "INTC", "pnl_usd": -7.24, "pnl_pct": -2.5, "result": "loss"},
            "fallback",
        )
        self.assertIn("FLIGHT CLOSED", out)
        self.assertNotIn("Never canned", out)
        self.assertTrue(telegram_structured_only(cfg))

    @patch.dict("os.environ", {"TELEGRAM_STRUCTURED_ONLY": "true"}, clear=False)
    def test_broadcast_exit_stays_structured_with_copilot_flag(self):
        cfg = BotConfig()
        comp = TelegramAIComposer(cfg)
        out = comp.compose_outbound(
            "commander_exit",
            {"ticker": "INTC", "pnl_usd": 12.0, "price": 25.5, "reason": "manual"},
            "EXIT INTC",
            copilot=True,
        )
        self.assertIn("COMMANDER EXIT", out)
        self.assertNotIn("Never canned", out)


if __name__ == "__main__":
    unittest.main()
