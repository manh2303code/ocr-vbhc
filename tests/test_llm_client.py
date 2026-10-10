import json
import unittest

import httpx

from llm_client import LlamaChatClient


def _sse(events):
    body = b"".join(
        b"data: " + json.dumps(event, ensure_ascii=False).encode("utf-8") + b"\n\n"
        for event in events
    )
    return body + b"data: [DONE]\n\n"


def _client_for(body, tokenize_response=None):
    def handler(request):
        if request.url.path == "/tokenize":
            if isinstance(tokenize_response, Exception):
                raise tokenize_response
            return tokenize_response or httpx.Response(
                200, json={"tokens": [1, 2, 3]}
            )
        return httpx.Response(
            200,
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

    def test_count_tokens_falls_back_when_tokenize_fails(self):
        client = _client_for(
            b"", tokenize_response=httpx.ConnectError("server unavailable")
        )
        self.assertEqual(client.count_tokens("mười hai ký tự"), len("mười hai ký tự") // 3)


if __name__ == "__main__":
    unittest.main()
