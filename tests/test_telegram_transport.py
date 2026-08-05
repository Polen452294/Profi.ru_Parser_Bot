import unittest
from unittest.mock import MagicMock, patch

from config import Settings
from telegram_transport import create_telegram_session


class TelegramTransportTests(unittest.TestCase):
    def test_local_dns_is_applied_to_socks_connector(self):
        settings = Settings.load(
            env_file=None,
            values={
                "TELEGRAM_PROXY": "socks5://127.0.0.1:20808",
                "TELEGRAM_PROXY_RDNS": "false",
            },
        )
        session = MagicMock()
        session._connector_init = {"rdns": True}

        with patch(
            "telegram_transport.AiohttpSession",
            return_value=session,
        ) as session_class:
            result = create_telegram_session(settings, timeout=30)

        self.assertIs(result, session)
        self.assertFalse(session._connector_init["rdns"])
        session_class.assert_called_once_with(
            proxy="socks5://127.0.0.1:20808",
            timeout=30,
        )


if __name__ == "__main__":
    unittest.main()
