"""Minimal air-gapped client for llama-server's OpenAI-compatible API."""

import json
import re
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

import httpx

# Qwen3 reads about 3.2 characters of lowercase Vietnamese per token (2.7
# without marks), but splits every digit and most capitals and symbols into
# a token of their own: 1360 characters of figures were 1043 tokens, and
# capitalised OCR headers ran at 1.8 characters per token. The estimate
# counts each digit, capital and symbol as a token and the other characters
# at 2.6 per token. Over 611 texts (corpus chunks, chat answers, the system
# prompt, the tool schemas) it was never below the real count (lowest 1.04x,
# median about 1.45x). Characters outside Latin and general punctuation count
# their UTF-8 bytes: ligatures, circled figures and roman numerals were at
# 0.34-0.46x of the real count under the letter rule, 1.13-1.16x now (and
# 1021 real texts stay at 1.045x or more).
SAFE_CHARS_PER_TOKEN = 2.6
# Chat-template tokens around each message (role markers, newlines).
_MESSAGE_OVERHEAD_TOKENS = 8
_CONTEXT_ERROR_RE = re.compile(r"context size|exceeds the available context", re.IGNORECASE)


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
    # The server's own counts, when it reports them.
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None


class LlamaServerError(RuntimeError):
    """The server reported an error inside a stream that had started."""


class ContextOverflowError(LlamaServerError):
    """The prompt (or prompt plus output) does not fit the server's context."""

    def __init__(self, n_prompt_tokens: Optional[int], n_ctx: Optional[int], message: str = ""):
        super().__init__(message or "request exceeds the available context size")
        self.n_prompt_tokens = n_prompt_tokens
        self.n_ctx = n_ctx


# Characters outside Latin (with Vietnamese letters and combining marks) and
# general punctuation, the ranges the estimate was checked on.
_UNMEASURED_RE = re.compile("[^\u0000-\u036f\u1e00-\u1eff\u2000-\u206f]")


def estimate_text_tokens(text: str) -> int:
    """A token count of text that errs high, never low: digits, capitals and
    symbols one each, lowercase letters and whitespace 2.6 per token. Other
    characters (ligatures, circled figures, other scripts) count their UTF-8
    bytes, the most a byte-level tokenizer makes of them."""
    other = _UNMEASURED_RE.findall(text)
    if other:
        text = _UNMEASURED_RE.sub("", text)
    single = sum(1 for ch in text if not (ch.islower() or ch.isspace()))
    rest = len(text) - single
    wide = sum(len(ch.encode("utf-8")) for ch in other)
    # ceil(rest / 2.6) in integers, so 26 characters are exactly 10 tokens.
    return wide + single - (-rest * 10 // int(round(SAFE_CHARS_PER_TOKEN * 10)))


def estimate_prompt_tokens(messages: List[dict], tools: Optional[List[dict]] = None) -> int:
    """A conservative token count of a chat request without asking the server."""
    total = 0
    for message in messages:
        total += estimate_text_tokens(str(message.get("content") or "")) + _MESSAGE_OVERHEAD_TOKENS
        for call in message.get("tool_calls") or []:
            function = call.get("function") or {}
            total += estimate_text_tokens(str(function.get("name") or ""))
            total += estimate_text_tokens(str(function.get("arguments") or ""))
    if tools:
        total += estimate_text_tokens(json.dumps(tools, ensure_ascii=False))
    return total


def _overflow_from_body(body: bytes) -> Optional[ContextOverflowError]:
    try:
        error = json.loads(body.decode("utf-8", "replace")).get("error") or {}
    except (ValueError, AttributeError):
        return None
    message = str(error.get("message") or "")
    if error.get("type") != "exceed_context_size_error" and not _CONTEXT_ERROR_RE.search(message):
        return None
    return ContextOverflowError(error.get("n_prompt_tokens"), error.get("n_ctx"), message)


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
        self._context_size = None  # type: Optional[int]

    def chat(
        self,
        messages: List[dict],
        tools: Optional[List[dict]] = None,
        stream_cb: Optional[Callable[[str], None]] = None,
        temperature: float = 0.2,
        max_tokens: int = 2048,
    ) -> ChatResponse:
        # top_k/top_p/min_p stay the server's defaults: Qwen's recommended
        # 20/0.8/0 scored the same over 41 questions x 5 runs (167/205).
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
        prompt_tokens = completion_tokens = None
        with self.client.stream(
            "POST",
            self.base_url + "/v1/chat/completions",
            json=payload,
            timeout=self.timeout,
        ) as response:
            if response.status_code == 400:
                overflow = _overflow_from_body(response.read())
                if overflow is not None:
                    raise overflow
            response.raise_for_status()
            for line in response.iter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if not data or data == "[DONE]":
                    continue
                event = json.loads(data)
                error = event.get("error")
                if error:
                    # A stream the server gave up on: never pass the half
                    # answer on as if it were complete.
                    message = str(error.get("message") if isinstance(error, dict) else error)
                    if _CONTEXT_ERROR_RE.search(message):
                        raise ContextOverflowError(None, None, message)
                    raise LlamaServerError(message)
                timings = event.get("timings")
                if timings:
                    prompt_tokens = int(timings.get("cache_n") or 0) + int(timings.get("prompt_n") or 0)
                    completion_tokens = int(timings.get("predicted_n") or 0)
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
        return ChatResponse(
            "".join(content_parts), tool_calls, finish_reason,
            prompt_tokens, completion_tokens,
        )

    def context_size(self) -> Optional[int]:
        """Context tokens per slot of the running server, or None."""
        if self._context_size is None:
            try:
                response = self.client.get(self.base_url + "/props", timeout=self.timeout)
                response.raise_for_status()
                n_ctx = response.json()["default_generation_settings"]["n_ctx"]
                self._context_size = int(n_ctx) if n_ctx else None
            except (httpx.HTTPError, KeyError, TypeError, ValueError):
                return None
        return self._context_size

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
            return estimate_text_tokens(text)

    def count_prompt_tokens(self, messages: List[dict], tools: Optional[List[dict]] = None) -> int:
        """Exact tokens of a request: the server renders its chat template
        (tool schemas included) and tokenizes the result."""
        body = {"messages": messages}
        if tools is not None:
            body["tools"] = tools
        try:
            response = self.client.post(
                self.base_url + "/apply-template", json=body, timeout=self.timeout,
            )
            response.raise_for_status()
            prompt = response.json()["prompt"]
            response = self.client.post(
                self.base_url + "/tokenize", json={"content": prompt}, timeout=self.timeout,
            )
            response.raise_for_status()
            return len(response.json()["tokens"])
        except (httpx.HTTPError, KeyError, TypeError, ValueError):
            return estimate_prompt_tokens(messages, tools)
