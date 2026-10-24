"""Chandra requests and the per-page quality gate.

Evidence (ocr_bench/research/chandra_ocr2.md, token_budget_audit.md):
the GGUF chat template opens a <think> block unless enable_thinking=false,
so a page can come back as English planning prose that hits max_tokens with
no HTML at all (bench page CV-1_p2; 2/64 pages of the live DB). A server
error in the middle of an SSE stream used to be skipped silently, and
finish_reason was only logged.
"""
import json
import unittest
from unittest.mock import patch

import httpx
from PIL import Image

import app_config
from llama_ocr import (
    LlamaOCR, OCRServerError, assess_page, find_repetition_loop, salvage_page,
)


GOOD = (
    '<div data-bbox="10 10 500 40" data-label="Text"><p>Số: 12/QĐ-UBND</p></div>\n'
    '<div data-bbox="10 50 900 90" data-label="Text"><p>Hưng Yên, ngày 24 tháng 5 năm 2017</p></div>'
)
PROSE = (
    "The user wants me to convert the image into a table. I will first extract "
    "the text from the image, including the header and the table, and then I "
    "need to format it into a structured HTML table with the right columns."
)
LOOP = "The table is a form for reporting assets and income of the official. " * 40
# A decoder loop inside the HTML: the same table row over and over.
ROW_LOOP = '<div data-label="Table"><table>' + "<tr><td>Nam châm</td><td>Chiếc</td></tr>\n" * 40


def _sse(*events):
    lines = ["data: " + json.dumps(event, ensure_ascii=False) for event in events]
    return ("\n\n".join(lines) + "\n\ndata: [DONE]\n\n").encode("utf-8")


def _delta(text):
    return {"choices": [{"index": 0, "delta": {"content": text}, "finish_reason": None}]}


def _finish(reason):
    return {"choices": [{"index": 0, "delta": {}, "finish_reason": reason}]}


def _client(handler):
    ocr = LlamaOCR(base_url="http://127.0.0.1:1")
    ocr._client.close()
    ocr._client = httpx.Client(transport=httpx.MockTransport(handler))
    return ocr


def _stream_reply(*events, sink=None):
    def handler(request):
        if sink is not None:
            sink.append(json.loads(request.content.decode("utf-8")))
        return httpx.Response(200, content=_sse(*events),
                              headers={"content-type": "text/event-stream"})
    return handler


IMAGE = Image.new("RGB", (64, 90), "white")


class ChandraRequestTests(unittest.TestCase):
    def test_streamed_page_request_disables_thinking(self):
        sent = []
        ocr = _client(_stream_reply(_delta(GOOD), _finish("stop"), sink=sent))

        ocr.ocr_page(IMAGE)

        self.assertEqual({"enable_thinking": False}, sent[0]["chat_template_kwargs"])

    def test_legacy_switch_restores_the_old_request(self):
        sent = []
        ocr = _client(_stream_reply(_delta(GOOD), _finish("stop"), sink=sent))

        with patch.object(app_config, "OCR_DISABLE_THINKING", False):
            ocr.ocr_page(IMAGE)

        self.assertNotIn("chat_template_kwargs", sent[0])

    def test_retry_uses_chandra_retry_sampling(self):
        sent = []
        ocr = _client(_stream_reply(_delta(GOOD), _finish("stop"), sink=sent))

        ocr.ocr_page(IMAGE)
        ocr.ocr_page(IMAGE, retry=True)

        self.assertEqual(0.0, sent[0]["temperature"])
        self.assertNotIn("top_p", sent[0])
        self.assertEqual(0.2, sent[1]["temperature"])
        self.assertEqual(0.95, sent[1]["top_p"])

    def test_clean_page_passes_with_its_finish_reason(self):
        ocr = _client(_stream_reply(_delta("<think>\n\n</think>\n\n"), _delta(GOOD),
                                    _finish("stop")))

        page = ocr.ocr_page(IMAGE)

        self.assertEqual(GOOD, page.html)
        self.assertEqual("stop", page.finish_reason)
        self.assertIsNone(page.problem)

    def test_sse_error_event_raises_instead_of_returning_a_partial_page(self):
        error = {"error": {"code": 500, "message": "Context size has been exceeded.",
                           "type": "server_error"}}
        ocr = _client(_stream_reply(_delta(GOOD[:40]), error))

        with self.assertRaises(OCRServerError):
            ocr.ocr_page(IMAGE)

    def test_length_finish_is_an_incomplete_page(self):
        ocr = _client(_stream_reply(_delta(GOOD), _finish("length")))

        self.assertEqual("truncated", ocr.ocr_page(IMAGE).problem)

    def test_stream_ending_without_a_finish_reason_is_incomplete(self):
        # llama-server always closes a finished generation with a
        # finish_reason; a stream that just stops was cut off.
        ocr = _client(_stream_reply(_delta(GOOD)))

        page = ocr.ocr_page(IMAGE)

        self.assertIsNone(page.finish_reason)
        self.assertEqual("truncated", page.problem)

    def test_reasoning_without_html_is_rejected(self):
        ocr = _client(_stream_reply(_delta("<think>\n" + PROSE), _finish("length")))

        page = ocr.ocr_page(IMAGE)

        self.assertEqual("no_html", page.problem)

    def test_stream_stops_early_on_a_repetition_loop(self):
        sentence = "The table is a form for reporting assets and income. "
        events = [_delta(GOOD)] + [_delta(sentence) for _ in range(2000)] + [_finish("length")]
        ocr = _client(_stream_reply(*events))

        page = ocr.ocr_page(IMAGE)

        self.assertEqual("repetition", page.problem)
        self.assertLess(page.n_tokens, 1000)


class PageGateTests(unittest.TestCase):
    def test_clean_vietnamese_page_passes(self):
        self.assertIsNone(assess_page(GOOD, "stop"))

    def test_empty_page(self):
        self.assertEqual("empty", assess_page("  ", "stop"))

    def test_page_without_html(self):
        self.assertEqual("no_html", assess_page(PROSE, "stop"))

    def test_english_reasoning_outside_the_layout_blocks(self):
        self.assertEqual("prose", assess_page(GOOD + "\n" + PROSE, "stop"))

    def test_english_document_text_inside_blocks_is_content(self):
        english = ('<div data-bbox="1 1 9 9" data-label="Text"><p>' + PROSE + "</p></div>")
        self.assertIsNone(assess_page(GOOD + english, "stop"))

    def test_repetition_loop(self):
        self.assertEqual("repetition", assess_page(GOOD + ROW_LOOP, "length"))

    def test_looping_reasoning_counts_as_prose(self):
        # The live DB pages: one English paragraph repeated ~190 times.
        self.assertEqual("prose", assess_page(GOOD + "\n" + LOOP, "length"))

    def test_length_without_other_damage_is_truncation(self):
        self.assertEqual("truncated", assess_page(GOOD, "length"))

    def test_blank_form_rows_and_dot_leaders_are_not_loops(self):
        rows = "<tr><td></td><td></td><td></td><td></td></tr>\n" * 40
        blank_table = '<div data-label="Table"><table>' + rows + "</table></div>"
        leaders = '<div data-label="Text"><p>Họ và tên: ' + "." * 400 + "</p></div>"
        self.assertIsNone(find_repetition_loop(GOOD + blank_table))
        self.assertIsNone(find_repetition_loop(GOOD + leaders))
        self.assertIsNone(assess_page(GOOD + blank_table + leaders, "stop"))

    def test_loop_start_is_located(self):
        start = find_repetition_loop(GOOD + LOOP)
        self.assertIsNotNone(start)
        self.assertGreaterEqual(start, len(GOOD))


class SalvageTests(unittest.TestCase):
    def test_reasoning_prose_is_never_kept(self):
        kept = salvage_page(GOOD + "\n" + PROSE, "prose")
        self.assertIn("12/QĐ-UBND", kept)
        self.assertNotIn("The user wants", kept)

    def test_page_without_html_keeps_nothing(self):
        self.assertEqual("", salvage_page(PROSE, "no_html"))

    def test_loop_tail_is_cut(self):
        kept = salvage_page(GOOD + "\n" + LOOP, "repetition")
        self.assertIn("12/QĐ-UBND", kept)
        self.assertLess(kept.count("reporting assets"), 2)

    def test_looping_row_is_kept_once(self):
        kept = salvage_page(GOOD + ROW_LOOP, "repetition")
        self.assertIn("12/QĐ-UBND", kept)
        self.assertEqual(1, kept.count("Nam châm"))

    def test_truncated_page_keeps_its_html(self):
        self.assertEqual(GOOD, salvage_page(GOOD, "truncated"))


if __name__ == "__main__":
    unittest.main()
