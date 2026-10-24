import json
import unittest

import httpx

from llm_client import (
    ContextOverflowError,
    LlamaChatClient,
    LlamaServerError,
    estimate_prompt_tokens,
)


def _sse(events):
    body = b"".join(
        b"data: " + json.dumps(event, ensure_ascii=False).encode("utf-8") + b"\n\n"
        for event in events
    )
    return body + b"data: [DONE]\n\n"


_OVERFLOW_BODY = json.dumps({"error": {
    "code": 400,
    "message": "request (31067 tokens) exceeds the available context size "
               "(16384 tokens), try increasing it",
    "type": "exceed_context_size_error",
    "n_prompt_tokens": 31067,
    "n_ctx": 16384,
}}).encode("utf-8")


def _client_for(body, tokenize_response=None, status=200, routes=None, seen=None):
    def handler(request):
        if seen is not None:
            seen.append(request)
        if routes and request.url.path in routes:
            route = routes[request.url.path]
            if isinstance(route, Exception):
                raise route
            return route(request) if callable(route) else route
        if request.url.path == "/tokenize":
            if isinstance(tokenize_response, Exception):
                raise tokenize_response
            return tokenize_response or httpx.Response(
                200, json={"tokens": [1, 2, 3]}
            )
        return httpx.Response(
            status,
            content=body,
            headers={"content-type": "text/event-stream"},
        )

    http_client = httpx.Client(
        transport=httpx.MockTransport(handler), base_url="http://local"
    )
    return LlamaChatClient("http://local", client=http_client)


class ChatClientTests(unittest.TestCase):
    def test_streams_content_and_sends_streaming_request(self):
        events = [
            {"choices": [{"delta": {"content": "Xin "}, "finish_reason": None}]},
            {"choices": [{"delta": {"content": "chào"}, "finish_reason": "stop"}]},
        ]
        chunks = []

        response = _client_for(_sse(events)).chat(
            [{"role": "user", "content": "hi"}], stream_cb=chunks.append
        )

        self.assertEqual(response.content, "Xin chào")
        self.assertEqual(chunks, ["Xin ", "chào"])
        self.assertEqual(response.finish_reason, "stop")
        self.assertEqual(response.tool_calls, [])

    def test_accumulates_fragmented_tool_calls_by_index(self):
        events = [
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 1,
                                    "id": "c2",
                                    "function": {
                                        "name": "check_validity",
                                        "arguments": '{"doc_',
                                    },
                                },
                                {
                                    "index": 0,
                                    "id": "c1",
                                    "function": {
                                        "name": "search_documents",
                                        "arguments": '{"que',
                                    },
                                },
                            ]
                        },
                        "finish_reason": None,
                    }
                ]
            },
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "function": {"arguments": 'ry":"hộ chiếu"}'},
                                },
                                {
                                    "index": 1,
                                    "function": {"arguments": 'id":42}'},
                                },
                            ]
                        },
                        "finish_reason": "tool_calls",
                    }
                ]
            },
        ]

        response = _client_for(_sse(events)).chat(
            [{"role": "user", "content": "q"}], tools=[]
        )

        self.assertEqual([call.id for call in response.tool_calls], ["c1", "c2"])
        self.assertEqual(
            [call.name for call in response.tool_calls],
            ["search_documents", "check_validity"],
        )
        self.assertEqual(json.loads(response.tool_calls[0].arguments), {"query": "hộ chiếu"})
        self.assertEqual(json.loads(response.tool_calls[1].arguments), {"doc_id": 42})
        self.assertEqual(response.finish_reason, "tool_calls")

    def test_count_tokens_uses_local_tokenize_endpoint(self):
        self.assertEqual(_client_for(b"").count_tokens("abc"), 3)

    def test_count_tokens_falls_back_conservatively_when_tokenize_fails(self):
        # Qwen3 reads about 3.2 characters of Vietnamese per token; 2.6 errs
        # on the side of counting too many, never too few.
        client = _client_for(
            b"", tokenize_response=httpx.ConnectError("server unavailable")
        )
        text = "mười hai ký tự" * 10
        self.assertEqual(client.count_tokens(text), -(-len(text) * 10 // 26))

    def test_sampling_keeps_the_server_defaults_at_a_low_temperature(self):
        seen = []
        _client_for(_sse([]), seen=seen).chat([{"role": "user", "content": "hi"}])

        payload = json.loads(seen[0].content.decode("utf-8"))
        self.assertEqual(payload["temperature"], 0.2)
        for knob in ("top_k", "top_p", "min_p"):
            self.assertNotIn(knob, payload)
        self.assertEqual(payload["max_tokens"], 2048)

    def test_prompt_larger_than_context_raises_context_overflow(self):
        client = _client_for(_OVERFLOW_BODY, status=400)

        with self.assertRaises(ContextOverflowError) as caught:
            client.chat([{"role": "user", "content": "q"}])

        self.assertEqual(caught.exception.n_prompt_tokens, 31067)
        self.assertEqual(caught.exception.n_ctx, 16384)

    def test_other_http_errors_still_raise_http_status_error(self):
        client = _client_for(b'{"error": {"code": 500, "message": "boom"}}', status=500)

        with self.assertRaises(httpx.HTTPStatusError):
            client.chat([{"role": "user", "content": "q"}])

    def test_error_event_inside_the_stream_is_not_swallowed(self):
        events = [
            {"choices": [{"delta": {"content": "Một nửa"}, "finish_reason": None}]},
            {"error": {"code": 500, "message": "Context size has been exceeded.",
                       "type": "server_error"}},
        ]
        with self.assertRaises(ContextOverflowError):
            _client_for(_sse(events)).chat([{"role": "user", "content": "q"}])

        events[1] = {"error": {"code": 500, "message": "out of memory", "type": "server_error"}}
        with self.assertRaises(LlamaServerError):
            _client_for(_sse(events)).chat([{"role": "user", "content": "q"}])

    def test_reports_the_servers_token_counts(self):
        events = [
            {"choices": [{"delta": {"content": "Có"}, "finish_reason": None}]},
            {"choices": [{"delta": {}, "finish_reason": "length"}],
             "timings": {"cache_n": 1000, "prompt_n": 234, "predicted_n": 56}},
        ]
        response = _client_for(_sse(events)).chat([{"role": "user", "content": "q"}])

        self.assertEqual(response.finish_reason, "length")
        self.assertEqual(response.prompt_tokens, 1234)
        self.assertEqual(response.completion_tokens, 56)

    def test_context_size_comes_from_the_running_server(self):
        props = httpx.Response(200, json={"default_generation_settings": {"n_ctx": 32768},
                                          "total_slots": 1})
        seen = []
        client = _client_for(b"", routes={"/props": props}, seen=seen)

        self.assertEqual(client.context_size(), 32768)
        self.assertEqual(client.context_size(), 32768)
        self.assertEqual(1, sum(1 for request in seen if request.url.path == "/props"))

    def test_context_size_is_none_when_the_server_cannot_say(self):
        client = _client_for(b"", routes={"/props": httpx.ConnectError("down")})

        self.assertIsNone(client.context_size())

    def test_prompt_tokens_are_counted_on_the_rendered_template_with_tools(self):
        seen = []
        routes = {
            "/apply-template": lambda request: httpx.Response(
                200, json={"prompt": "<rendered>" + json.loads(request.content)["messages"][0]["content"]}
            ),
            "/tokenize": lambda request: httpx.Response(
                200, json={"tokens": list(range(len(json.loads(request.content)["content"])))}
            ),
        }
        client = _client_for(b"", routes=routes, seen=seen)
        messages = [{"role": "user", "content": "abcd"}]
        tools = [{"type": "function", "function": {"name": "t"}}]

        self.assertEqual(client.count_prompt_tokens(messages, tools), len("<rendered>abcd"))
        template_request = next(r for r in seen if r.url.path == "/apply-template")
        self.assertEqual(json.loads(template_request.content)["tools"], tools)

    def test_prompt_tokens_fall_back_to_a_conservative_estimate(self):
        client = _client_for(b"", routes={"/apply-template": httpx.ConnectError("down")})
        messages = [{"role": "system", "content": "x" * 260}, {"role": "user", "content": "y" * 26}]

        counted = client.count_prompt_tokens(messages)

        self.assertEqual(counted, estimate_prompt_tokens(messages))
        self.assertGreaterEqual(counted, 110)

    def test_estimate_counts_digits_capitals_and_symbols_as_a_token_each(self):
        # Measured with Qwen3's own tokenizer: 1360 characters of figures and
        # dates were 1043 tokens, which chars / 2.6 put at 524; capitalised OCR
        # headers undercounted by up to 30 %. Digits, capitals and symbols
        # count one token each; lowercase letters and spaces 2.6 per token.
        def text_tokens(text):
            return estimate_prompt_tokens([{"role": "user", "content": text}]) - \
                estimate_prompt_tokens([{"role": "user", "content": ""}])

        self.assertEqual(text_tokens("1234567890"), 10)
        self.assertEqual(text_tokens("CỘNG HÒA"), 7 + 1)  # 7 capitals, 1 space
        self.assertEqual(text_tokens("12/05/2019;"), 11)
        self.assertEqual(text_tokens("mười hai ký tự" * 10), -(-140 * 10 // 26))
        figures = "Tổng kinh phí: 1.234.567.890 đồng; 12/05/2019; 45/2019/NĐ-CP; 0,85; " * 20
        self.assertGreaterEqual(text_tokens(figures), 1043)

    def test_estimate_counts_characters_outside_latin_as_their_bytes(self):
        # A byte-level tokenizer makes at most one token per UTF-8 byte. The
        # letter rule put a line of ligatures, circled figures and roman
        # numerals at 0.44x of Qwen3's count; Vietnamese and its punctuation
        # keep the measured rule.
        def text_tokens(text):
            return estimate_prompt_tokens([{"role": "user", "content": text}]) -                 estimate_prompt_tokens([{"role": "user", "content": ""}])

        for text in ("ﬁ", "ﬂ", "①", "ⅰ", "Ⅳ", "→", "✓", "α", "中"):
            self.assertEqual(text_tokens(text * 10), 10 * len(text.encode("utf-8")), text)
        # Four symbols, a capital and a digit at 1 each; 17 others at 2.6 a token.
        self.assertEqual(text_tokens("đây là “Điều 1” – mục …"), 6 + 7)

    def test_estimate_counts_tool_schemas_and_tool_calls(self):
        bare = estimate_prompt_tokens([{"role": "user", "content": "q"}])
        with_tools = estimate_prompt_tokens(
            [{"role": "user", "content": "q"}],
            [{"type": "function", "function": {"name": "search", "description": "d" * 520}}],
        )
        with_call = estimate_prompt_tokens([
            {"role": "user", "content": "q"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "c1", "type": "function",
                 "function": {"name": "search", "arguments": '{"query": "' + "z" * 260 + '"}'}}]},
        ])
        self.assertGreater(with_tools, bare + 200)
        self.assertGreater(with_call, bare + 100)


if __name__ == "__main__":
    unittest.main()
