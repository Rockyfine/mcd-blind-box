"""确认门禁的测试。

这是整个项目的安全底线：**拿不准就一律判为不确认**。
宁可多问一句，也不要误下单。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcd_blind_box.confirmation import (  # noqa: E402
    NEGATION_PREFIXES,
    classify,
    is_write_tool,
    require_order_confirmation,
)
from mcd_blind_box.constants import (  # noqa: E402
    ALLOWED_TOOLS,
    CONFIRM_BLACKLIST_APPROVAL,
    CONFIRM_BLACKLIST_HESITANT,
    CONFIRM_WHITELIST,
    READ_ONLY_TOOLS,
    WRITE_TOOLS,
)


class TestWriteToolInventory(unittest.TestCase):
    def test_write_and_readonly_partition_all_tools(self):
        self.assertEqual(WRITE_TOOLS & READ_ONLY_TOOLS, frozenset())
        self.assertEqual(WRITE_TOOLS | READ_ONLY_TOOLS, ALLOWED_TOOLS)

    def test_create_order_is_write(self):
        self.assertTrue(is_write_tool("create-order"))

    def test_other_writes(self):
        for name in (
            "cancel-order",
            "delivery-create-address",
            "auto-bind-coupons",
            "draw-lottery",
            "mall-create-order",
            "party-order-create",
        ):
            with self.subTest(name=name):
                self.assertTrue(is_write_tool(name))

    def test_calculate_price_is_not_write(self):
        """算价只算价、不下单，属于纯查询。"""
        self.assertFalse(is_write_tool("calculate-price"))

    def test_reads_are_not_write(self):
        for name in ("query-meals", "query-store-coupons", "now-time-info", "query-order"):
            with self.subTest(name=name):
                self.assertFalse(is_write_tool(name))

    def test_unknown_tool_is_not_write(self):
        self.assertFalse(is_write_tool("definitely-not-a-tool"))


class TestClassifyEmpty(unittest.TestCase):
    def test_none_and_blank(self):
        for value in (None, "", "   ", "\u3000", "\t\n"):
            with self.subTest(value=value):
                self.assertFalse(classify(value).confirmed)


class TestClassifyWhitelist(unittest.TestCase):
    def test_every_whitelist_phrase_confirms(self):
        for phrase in CONFIRM_WHITELIST:
            with self.subTest(phrase=phrase):
                verdict = classify(phrase)
                self.assertTrue(verdict.confirmed, verdict.reason)

    def test_phrase_inside_a_sentence(self):
        self.assertTrue(classify("好的，帮我下单吧").confirmed)

    def test_full_width_spaces_are_ignored(self):
        self.assertTrue(classify("确认\u3000下单").confirmed)

    def test_surrounding_whitespace(self):
        self.assertTrue(classify("  确认下单  ").confirmed)


class TestClassifyNegation(unittest.TestCase):
    def test_each_negation_prefix_blocks(self):
        for prefix in NEGATION_PREFIXES:
            with self.subTest(prefix=prefix):
                self.assertFalse(classify(f"{prefix}确认下单").confirmed)

    def test_explicit_cases(self):
        for text in ("不确认", "别确认", "先别下单吧", "暂时不买了", "不想买了", "取消确认"):
            with self.subTest(text=text):
                self.assertFalse(classify(text).confirmed)

    def test_negation_reason_mentions_negation(self):
        verdict = classify("不确认")
        self.assertIn("否定", verdict.reason)


class TestClassifyApprovalOnly(unittest.TestCase):
    def test_every_approval_phrase_is_rejected(self):
        for phrase in CONFIRM_BLACKLIST_APPROVAL:
            with self.subTest(phrase=phrase):
                self.assertFalse(classify(phrase).confirmed, phrase)

    def test_trailing_punctuation_still_rejected(self):
        for text in ("行。", "好吧！", "嗯~", "看起来不错。"):
            with self.subTest(text=text):
                self.assertFalse(classify(text).confirmed)

    def test_approval_phrase_with_extra_words_is_still_not_confirmation(self):
        """「就这个吧挺好的」不是白名单整句，也不是白名单短语，一律不确认。"""
        self.assertFalse(classify("就这个吧挺好的").confirmed)


class TestClassifyHesitant(unittest.TestCase):
    def test_every_hesitant_phrase_is_rejected(self):
        for phrase in CONFIRM_BLACKLIST_HESITANT:
            with self.subTest(phrase=phrase):
                self.assertFalse(classify(phrase).confirmed, phrase)

    def test_hesitant_wins_over_whitelist(self):
        # 句子同时含「下单」和犹豫语气，必须先判犹豫
        verdict = classify("如果可以的话就下单吧")
        self.assertFalse(verdict.confirmed)
        self.assertIn("条件式", verdict.reason)


class TestClassifyQuestion(unittest.TestCase):
    def test_questions_are_rejected(self):
        for text in (
            "多少钱？",
            "这份热量多少",
            "可以吗",
            "能下单吗",
            "怎么下单",
            "为什么是这个",
            "这是什么",
            "有哪些选择",
            "确认下单吗？",
        ):
            with self.subTest(text=text):
                self.assertFalse(classify(text).confirmed, text)

    def test_question_reason(self):
        self.assertIn("提问", classify("可以吗").reason)


class TestClassifyExclamation(unittest.TestCase):
    def test_exclamations_are_rejected(self):
        for text in ("哇这个太好了", "哈哈笑死", "太惊喜了"):
            with self.subTest(text=text):
                self.assertFalse(classify(text).confirmed, text)

    def test_confirmation_with_exclamation_still_confirms(self):
        # 白名单在前，所以「确认下单！」算确认
        self.assertTrue(classify("确认下单！").confirmed)


class TestClassifyDefault(unittest.TestCase):
    def test_ambiguous_input_defaults_to_no(self):
        for text in ("随便", "再看看", "先这样", "嗯嗯嗯", "收到"):
            with self.subTest(text=text):
                self.assertFalse(classify(text).confirmed, text)

    def test_default_reason_explains(self):
        self.assertIn("默认不下单", classify("随便").reason)


class TestRequireOrderConfirmation(unittest.TestCase):
    def test_flag_alone_confirms(self):
        self.assertTrue(require_order_confirmation(True).confirmed)

    def test_no_flag_no_text_rejects(self):
        self.assertFalse(require_order_confirmation(False).confirmed)

    def test_blank_text_falls_back_to_flag(self):
        self.assertTrue(require_order_confirmation(True, "   ").confirmed)
        self.assertFalse(require_order_confirmation(False, "   ").confirmed)

    def test_text_alone_can_confirm(self):
        self.assertTrue(require_order_confirmation(False, "确认下单").confirmed)

    def test_text_overrides_flag(self):
        """带了 --confirm 但原话只是「好的」，仍然拒绝。"""
        verdict = require_order_confirmation(True, "好的")
        self.assertFalse(verdict.confirmed)
        self.assertIn("默认不下单", verdict.reason)

    def test_text_approval_blacklist_overrides_flag(self):
        """规范点名的认可短语，理由更具体。"""
        verdict = require_order_confirmation(True, "就这个吧")
        self.assertFalse(verdict.confirmed)
        self.assertIn("认可", verdict.reason)

    def test_text_negation_overrides_flag(self):
        self.assertFalse(require_order_confirmation(True, "先别下单吧").confirmed)

    def test_verdict_carries_matched_phrase(self):
        self.assertEqual(require_order_confirmation(True, "确认下单").matched, "确认下单")

    def test_flag_verdict_matched_marker(self):
        self.assertEqual(require_order_confirmation(True).matched, "--confirm")


if __name__ == "__main__":
    unittest.main(verbosity=2)
