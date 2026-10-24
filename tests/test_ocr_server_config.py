"""Chandra llama-server context layout.

Without -np, llama-server picked 4 slots sharing one 8192-cell KV pool
(kv_unified): a landscape and a portrait page OCR'd together overflowed it
and both requests got HTTP 500 "Context size has been exceeded"
(chandra_ocr2.md §4.5). One slot per OCR worker, each with its own 12288
cells, removes the shared pool; the prompt cache in RAM never hits for
unique page images and grew to 8 GiB, so it is switched off.

Every OCR request also disables Qwen3.5's thinking block: with it on, a page
can come back as English planning prose and no HTML (bench page CV-1_p2,
2/64 pages of the live DB; chandra_ocr2.md §4.3).
"""
import json
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from PIL import Image

import app_config
import ocr_pipeline
from llama_ocr import LlamaOCR
from llama_server_manager import build_server_command
from ocr_pipeline import DocumentOCR


def _cmd(slots, backend="cuda"):
    return build_server_command(
        Path("llama-server.exe"), Path("model.gguf"), Path("mmproj.gguf"),
        port=9990, backend=backend, parallel_slots=slots,
    )


def _value(cmd, flag):
    return cmd[cmd.index(flag) + 1] if flag in cmd else None


class OCRServerContextTests(unittest.TestCase):
    def test_one_slot_per_worker_with_its_own_context(self):
        cmd = _cmd(2)

        self.assertEqual("2", _value(cmd, "-np"))
        self.assertEqual(str(2 * 12288), _value(cmd, "-c"))
        self.assertEqual("0", _value(cmd, "--cache-ram"))

    def test_single_worker(self):
        cmd = _cmd(1, backend="cpu")

        self.assertEqual("1", _value(cmd, "-np"))
        self.assertEqual("12288", _value(cmd, "-c"))
        self.assertEqual("0", _value(cmd, "-ngl"))

    def test_legacy_layout_restores_the_old_flags(self):
        with patch.object(app_config, "OCR_KV_LAYOUT", "legacy"):
            cmd = _cmd(2)

        self.assertEqual("8192", _value(cmd, "-c"))
        self.assertNotIn("-np", cmd)
        self.assertNotIn("--cache-ram", cmd)

    def test_reasoning_stays_in_content_for_the_client_to_strip(self):
        self.assertEqual("none", _value(_cmd(1), "--reasoning-format"))


GOOD = '<div data-bbox="10 10 500 40" data-label="Text"><p>Số: 12/QĐ-UBND</p></div>'
IMAGE = Image.new("RGB", (64, 90), "white")


def _client(handler):
    ocr = LlamaOCR(base_url="http://127.0.0.1:1")
    ocr._client.close()
    ocr._client = httpx.Client(transport=httpx.MockTransport(handler))
    return ocr


def _recording_handler(sent):
    def handler(request):
        payload = json.loads(request.content.decode("utf-8"))
        sent.append(payload)
        if payload.get("stream"):
            events = [
                {"choices": [{"delta": {"content": GOOD}, "finish_reason": None}]},
                {"choices": [{"delta": {}, "finish_reason": "stop"}]},
            ]
            body = "".join("data: " + json.dumps(event) + "\n\n" for event in events)
            body += "data: [DONE]\n\n"
            return httpx.Response(200, content=body.encode("utf-8"),
                                  headers={"content-type": "text/event-stream"})
        return httpx.Response(200, json={"choices": [
            {"message": {"content": GOOD}, "finish_reason": "stop"}]})
    return handler


class ChandraRequestThinkingTests(unittest.TestCase):
    def _send_all(self):
        sent = []
        ocr = _client(_recording_handler(sent))
        ocr.extract_text(IMAGE)
        ocr._extract_raw(IMAGE, None)
        ocr._extract_raw_streaming(IMAGE, None, on_token=lambda *_: True)
        return sent

    def test_every_request_disables_thinking(self):
        sent = self._send_all()

        self.assertEqual(3, len(sent))
        for payload in sent:
            self.assertEqual({"enable_thinking": False}, payload["chat_template_kwargs"])

    def test_legacy_switch_restores_the_old_request(self):
        with patch.object(app_config, "OCR_DISABLE_THINKING", False):
            sent = self._send_all()

        for payload in sent:
            self.assertNotIn("chat_template_kwargs", payload)


class _FakeServer:
    starts = []

    def __init__(self, port):
        self.port = port

    def start(self, **kwargs):
        _FakeServer.starts.append(kwargs)


class _FakeClient:
    def __init__(self, base_url):
        self.base_url = base_url


class WorkerSlotTests(unittest.TestCase):
    def setUp(self):
        _FakeServer.starts = []

    def _init(self, backend, workers):
        with patch.object(ocr_pipeline, "LlamaServerManager", _FakeServer), \
             patch.object(ocr_pipeline, "LlamaOCR", _FakeClient), \
             patch.object(ocr_pipeline, "select_backend", return_value=backend), \
             patch("hardware_detect.detect_hardware", return_value=None), \
             patch("hardware_detect.calculate_optimal_workers", return_value=workers):
            engine = DocumentOCR.__new__(DocumentOCR)
            engine._init_hybrid()
        return engine

    def test_server_gets_one_slot_per_ocr_worker(self):
        engine = self._init("cuda", 2)

        self.assertEqual(2, engine.ocr_workers)
        self.assertEqual(2, _FakeServer.starts[0]["parallel_slots"])
        self.assertEqual("cuda", _FakeServer.starts[0]["backend"])

    def test_cpu_backend_runs_one_worker(self):
        engine = self._init("cpu", 2)

        self.assertEqual(1, engine.ocr_workers)
        self.assertEqual(1, _FakeServer.starts[0]["parallel_slots"])


if __name__ == "__main__":
    unittest.main()
