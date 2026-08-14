import unittest

from filters import MIN_BUDGET_RUB, evaluate_order, order_matches_filter


class ServiceFilterTests(unittest.TestCase):
    def test_requested_topics_are_accepted(self):
        accepted = (
            "Нужно разработать Telegram-бота, цена 5 000 рублей",
            "Требуется создать бота для телеграма",
            "Нужно написать MAX-бота, бюджет 8 000 ₽",
            "Нужен бот для мессенджера Макс",
            "Надо написать парсер каталога, бюджет 25 000 ₽",
            "Требуется парсинг сайта, стоимость 6000 руб.",
            "Требуется разработка CRM-системы, бюджет 120000 руб.",
        )
        for text in accepted:
            with self.subTest(text=text):
                self.assertTrue(order_matches_filter({"title": text}))

    def test_target_group_is_reported(self):
        samples = (
            ("Нужно создать Telegram-бота", "Telegram-боты"),
            ("Нужно создать бота для MAX", "MAX-боты"),
            ("Нужно написать парсер", "Парсеры и парсинг"),
            ("Нужно разработать CRM", "Разработка CRM"),
        )
        for text, expected_group in samples:
            with self.subTest(text=text):
                decision = evaluate_order(text)
                self.assertTrue(decision.accepted)
                self.assertEqual(decision.matched_rule.group, expected_group)

    def test_development_intent_is_required(self):
        rejected = (
            "У нас уже есть CRM",
            "Обзор Telegram-ботов",
            "Статья про парсеры данных",
        )
        for text in rejected:
            with self.subTest(text=text):
                self.assertFalse(order_matches_filter(text))

    def test_unrequested_topics_are_rejected(self):
        rejected = (
            "Нужно создать бота для ВКонтакте, цена 15000 рублей",
            "Хочу автоматизировать обработку заявок, бюджет 30000 рублей",
            "Нужно разработать интернет-магазин, стоимость 100000 рублей",
        )
        for text in rejected:
            with self.subTest(text=text):
                self.assertFalse(order_matches_filter(text))

    def test_advertising_exclusions_are_preserved(self):
        rejected = (
            "Нужно разработать Telegram-бота для таргетинга, бюджет 50000 рублей",
            "Нужно создать MAX-бота: контекстная реклама, бюджет 50000 рублей",
            "Нужно создать CRM для SMM, бюджет 50000 рублей",
        )
        for text in rejected:
            with self.subTest(text=text):
                self.assertFalse(order_matches_filter(text))

    def test_platform_exclusions_are_preserved(self):
        for platform in ("Instagram", "WhatsApp", "Facebook", "Discord"):
            text = (
                f"Нужно разработать Telegram-бота с интеграцией {platform}, "
                "бюджет 50000 рублей"
            )
            with self.subTest(platform=platform):
                decision = evaluate_order(text)
                self.assertFalse(decision.accepted)
                self.assertIsNotNone(decision.excluded_rule)

    def test_budget_is_optional_or_at_least_five_thousand(self):
        self.assertEqual(MIN_BUDGET_RUB, 5_000)
        self.assertFalse(
            order_matches_filter("Нужно написать парсер, бюджет 4 999 рублей")
        )
        self.assertTrue(
            order_matches_filter("Нужно написать парсер, бюджет 5 000 рублей")
        )
        self.assertTrue(order_matches_filter("Нужно написать парсер каталога"))
        self.assertTrue(
            order_matches_filter("Нужно написать парсер, цена не указана")
        )

    def test_numeric_price_field_obeys_budget_limit(self):
        base_order = {"title": "Нужно разработать Telegram-бота"}
        self.assertFalse(order_matches_filter({**base_order, "price": 4_999}))
        self.assertTrue(order_matches_filter({**base_order, "price": 5_000}))

    def test_diagnostic_decision_matches_boolean_filter(self):
        samples = (
            "Нужно разработать CRM, бюджет 5000 рублей",
            "Нужно разработать CRM для таргетинга, бюджет 50000 рублей",
            "Нужно создать Telegram-бота, цена 4999 рублей",
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
