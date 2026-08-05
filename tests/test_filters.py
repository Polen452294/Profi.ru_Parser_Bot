import unittest

from filters import evaluate_order, order_matches_filter


class OriginalTopicFilterTests(unittest.TestCase):
    def test_original_target_topics_are_preserved(self):
        accepted = (
            "Нужно разработать Telegram-бота, бюджет 50 000 рублей",
            "Требуется внедрить CRM-систему, бюджет 120000 руб.",
            "Надо написать парсер каталога, бюджет 25 000 ₽",
            "Хочу автоматизировать обработку заявок, бюджет 30000 рублей",
        )
        for text in accepted:
            with self.subTest(text=text):
                self.assertTrue(order_matches_filter({"title": text}))

    def test_development_intent_is_still_required(self):
        self.assertFalse(order_matches_filter("У нас уже есть CRM и бот"))

    def test_original_advertising_exclusions_are_preserved(self):
        rejected = (
            "Нужно разработать бота для таргетинга, бюджет 50000 рублей",
            "Нужно создать бота: контекстная реклама, бюджет 50000 рублей",
            "Нужно создать CRM для SMM, бюджет 50000 рублей",
        )
        for text in rejected:
            with self.subTest(text=text):
                self.assertFalse(order_matches_filter(text))

    def test_original_platform_exclusions_are_preserved(self):
        for platform in ("Instagram", "WhatsApp", "Facebook", "Discord"):
            text = f"Нужно разработать бота для {platform}, бюджет 50000 рублей"
            with self.subTest(platform=platform):
                self.assertFalse(order_matches_filter(text))

    def test_original_budget_rule_is_preserved(self):
        self.assertFalse(
            order_matches_filter("Нужно разработать парсер, бюджет 9 000 рублей")
        )
        self.assertTrue(
            order_matches_filter("Нужно разработать парсер, бюджет 10 000 рублей")
        )
        self.assertTrue(order_matches_filter("Нужно разработать парсер каталога"))

    def test_diagnostic_decision_matches_original_boolean_filter(self):
        samples = (
            "Нужно разработать CRM, бюджет 50000 рублей",
            "Нужно разработать CRM для таргетинга, бюджет 50000 рублей",
            "Просто консультация",
            "",
        )
        for text in samples:
            with self.subTest(text=text):
                self.assertEqual(
                    evaluate_order(text).accepted,
                    order_matches_filter(text),
                )


if __name__ == "__main__":
    unittest.main()
