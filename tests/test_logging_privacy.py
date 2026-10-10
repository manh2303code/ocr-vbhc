import logging
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import chandra_field_parser


class LoggingPrivacyTests(unittest.TestCase):
    def test_logging_configuration_is_rotating_and_idempotent(self):
        from logging_config import configure_logging

        with tempfile.TemporaryDirectory() as tmp:
            configure_logging(Path(tmp))
            configure_logging(Path(tmp))
            handlers = [
                handler for handler in logging.getLogger().handlers
                if getattr(handler, "_ocr_vbhc_handler", False)
            ]
            try:
                self.assertEqual(2, len(handlers))  # rotating file + console
                self.assertTrue(any(hasattr(handler, "maxBytes") for handler in handlers))
            finally:
                for handler in handlers:
                    logging.getLogger().removeHandler(handler)
                    handler.close()

    def test_parser_never_logs_raw_ocr_content(self):
        secret = "NOI_DUNG_MAT_KHONG_DUOC_GHI_LOG"
        html = f'<div data-label="Text" data-bbox="1 1 10 10"><p>{secret}</p></div>'

        with patch.object(chandra_field_parser.logger, "info") as info:
            chandra_field_parser.parse_chandra_html([html])

        logged = " ".join(str(call) for call in info.call_args_list)
        self.assertNotIn(secret, logged)


if __name__ == "__main__":
    unittest.main()
