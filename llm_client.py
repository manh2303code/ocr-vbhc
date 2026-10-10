"""Minimal air-gapped client for llama-server's OpenAI-compatible API."""

import json
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

import httpx


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str


@dataclass
class ChatResponse:
    content: str
    tool_calls: List[ToolCall]
    finish_reason: str


class LlamaChatClient:
    def __init__(
        self,
        base_url: str,
        timeout: float = 300.0,
        client: Optional[httpx.Client] = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.client = client or httpx.Client()

    def chat(
        self,
        messages: List[dict],
        tools: Optional[List[dict]] = None,
        stream_cb: Optional[Callable[[str], None]] = None,
        temperature: float = 0.2,
        max_tokens: int = 2048,
    ) -> ChatResponse:
        payload = {
            "messages": messages,
            "stream": True,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools is not None:
            payload["tools"] = tools

        content_parts = []  # type: List[str]
        calls = {}  # type: Dict[int, Dict[str, str]]
        finish_reason = ""
        with self.client.stream(
            "POST",
            self.base_url + "/v1/chat/completions",
            json=payload,
            timeout=self.timeout,
        ) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if not data or data == "[DONE]":
                    continue
                event = json.loads(data)
                for choice in event.get("choices", []):
                    if choice.get("finish_reason") is not None:
                        finish_reason = choice["finish_reason"]
                    delta = choice.get("delta") or {}
                    part = delta.get("content")
                    if part:
                        content_parts.append(part)
                        if stream_cb:
                            stream_cb(part)
                    for fragment in delta.get("tool_calls") or []:
                        index = fragment.get("index", 0)
                        call = calls.setdefault(
                            index, {"id": "", "name": "", "arguments": ""}
                        )
                        if fragment.get("id"):
                            call["id"] += fragment["id"]
                        function = fragment.get("function") or {}
                        if function.get("name"):
                            call["name"] += function["name"]
                        if function.get("arguments"):
                            call["arguments"] += function["arguments"]

        tool_calls = [
            ToolCall(calls[index]["id"], calls[index]["name"], calls[index]["arguments"])
            for index in sorted(calls)
        ]
        return ChatResponse("".join(content_parts), tool_calls, finish_reason)

    def count_tokens(self, text: str) -> int:
        try:
            response = self.client.post(
                self.base_url + "/tokenize",
                json={"content": text},
                timeout=self.timeout,
            )
            response.raise_for_status()
            return len(response.json()["tokens"])
        except (httpx.HTTPError, KeyError, TypeError, ValueError):
            return len(text) // 3
