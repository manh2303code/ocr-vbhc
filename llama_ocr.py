"""
OCR engine using llama-server HTTP API (OpenAI-compatible).
Drop-in replacement for VinternOCR — same extract_text() interface.
"""
import io
import atexit
import json
import re
import base64
import logging
import time
from dataclasses import dataclass
from typing import Optional, Tuple, Union

import httpx
import numpy as np
from PIL import Image

import app_config
from app_config import (
    ACTIVE_OCR_MODEL, LLAMA_SERVER_URL, OCR_MAX_TOKENS, OCR_TEMPERATURE,
    OCR_REPEAT_PENALTY,
    get_ocr_prompt, get_ocr_system_prompt,
)

logger = logging.getLogger("LlamaOCR")

# NOTE: Per-field CLASS_PROMPTS removed — Chandra-only pipeline parses HTML
# instead of per-region OCR. See chandra_field_parser.py.


class OCRServerError(RuntimeError):
    """llama-server reported an error inside the SSE stream.

    Seen as ``data: {"error": {"code": 500, "message": "Context size has
    been exceeded."}}`` with no finish_reason and no [DONE]; the text
    received so far is an incomplete page and must not be used as one.
    """


@dataclass
class PageOCR:
    """One page as returned by the model, with what the gate made of it."""

    html: str
    finish_reason: Optional[str] = None
    n_tokens: int = 0
    problem: Optional[str] = None


# Reasons the quality gate rejects a page, in the words the UI shows.
PAGE_PROBLEM_LABELS = {
    "error": "máy chủ OCR báo lỗi",
    "empty": "không có nội dung",
    "no_html": "mô hình trả lời bằng lời giải thích thay cho văn bản",
    "prose": "lẫn lời suy luận của mô hình",
    "repetition": "mô hình lặp vòng",
    "truncated": "bị cắt cụt, có thể thiếu nội dung",
}
# Which attempt to keep when both fail: lower is more usable.
PAGE_PROBLEM_SEVERITY = {
    None: 0, "truncated": 1, "repetition": 2, "prose": 3,
    "no_html": 4, "empty": 5, "error": 5,
}

_HTML_BLOCK_RE = re.compile(r"<(?:div|p|table|tr|td|th|h[1-6]|ul|ol|li)\b", re.IGNORECASE)
_REASONING_RE = re.compile(
    r"\b(?:The user wants|I need to|I will|I'll|Let me|Let's|I should|Looking at the)\b"
)
# English function words; none of them is also a common Vietnamese syllable
# ("an", "in", "can", "to", "do" are, so they are left out).
_EN_FUNCTION_WORDS = frozenset((
    "the", "and", "or", "of", "for", "with", "is", "are", "was", "be", "been",
    "this", "that", "these", "it", "its", "we", "you", "me", "my", "will",
    "need", "needs", "should", "would", "could", "let", "first", "then",
    "user", "wants", "image", "which", "there", "from", "into", "each",
))
_LOOP_WINDOW = 4000      # chars of tail examined for a loop
_LOOP_MAX_UNIT = 500     # longest repeating unit considered
_LOOP_MIN_SPAN = 400     # a loop must cover at least this many chars
_LOOP_CHECK_EVERY = 200  # streamed tokens between loop checks


def strip_reasoning(content: str) -> str:
    """Keep the HTML the model produced, drop its <think> block.

    With --reasoning-format none the whole generation is in content.
    Chandra (Qwen 3.5) may embed <think>...</think> there:
      1) <think><div ...>OCR</div>... (no closing) — direct OCR after tag
      2) <think>English thinking...\\n<div>OCR</div>... (no closing)
      3) <think>thinking</think><div>OCR</div>... — clean split; also the
         empty "<think>\\n\\n</think>" block when thinking is disabled.
    OCR HTML lives after </think>; fall back to the thinking part only if
    it is the one holding <div>. Text before the first <div is dropped.
    """
    content = content or ""
    if "<think>" not in content:
        return content.strip()
    m = re.search(r'<think>(.*?)</think>(.*)', content, flags=re.DOTALL)
    if m:
        thinking_text = m.group(1).strip()
        after_text = m.group(2).strip()
        if "<div" in after_text:
            text = after_text
        elif "<div" in thinking_text:
            text = thinking_text
        else:
            text = after_text or thinking_text
    else:
        text = content.replace("<think>", "", 1).strip()
    if "<div" in text:
        div_pos = text.index("<div")
        if div_pos > 0:
            logger.info(f"Stripped {div_pos} chars of preamble before <div>")
            text = text[div_pos:]
    return text.strip()


def _top_level_text(html: str) -> str:
    """Text sitting outside every element: Chandra puts content in blocks."""
    from bs4 import BeautifulSoup
    from bs4.element import Comment, NavigableString

    soup = BeautifulSoup(html, "html.parser")
    return " ".join(
        str(node) for node in soup.contents
        if isinstance(node, NavigableString) and not isinstance(node, Comment)
    ).strip()


def looks_like_reasoning(text: str) -> bool:
    words = re.findall(r"[^\W\d_]+", text or "")
    if len(words) < 8:
        return False
    if _REASONING_RE.search(text):
        return True
    english = sum(1 for word in words if word.lower() in _EN_FUNCTION_WORDS)
    return english / len(words) >= 0.2


def _visible_alnum(fragment: str) -> int:
    text = re.sub(r"<[^<>]*>", "", fragment)
    text = re.sub(r"^[^<>]*>", "", text)   # tag cut at the start
    text = re.sub(r"<[^<>]*$", "", text)   # tag cut at the end
    return sum(1 for ch in text if ch.isalnum())


def _find_loop(text: str) -> Optional[Tuple[int, int]]:
    """(start, unit_length) of a looping tail, or None."""
    if not text:
        return None
    tail = text[-_LOOP_WINDOW:]
    n = len(tail)
    for unit_len in range(1, min(_LOOP_MAX_UNIT, n // 2) + 1):
        unit = tail[n - unit_len:]
        repeats = 1
        pos = n - 2 * unit_len
        while pos >= 0 and tail[pos:pos + unit_len] == unit:
            repeats += 1
            pos -= unit_len
        needed = int(4 * (1 + 3.0 / unit_len))
        if repeats <= needed or repeats * unit_len < _LOOP_MIN_SPAN:
            continue
        if _visible_alnum(unit) < 2:
            continue
        start = len(text) - repeats * unit_len
        while start - unit_len >= 0 and text[start - unit_len:start] == unit:
            start -= unit_len
        return start, unit_len
    return None


def find_repetition_loop(text: str) -> Optional[int]:
    """Index where a back-to-back repeated tail starts, or None.

    Same rule as Chandra's detect_repeat_token (chandra/model/util.py,
    Apache-2.0): the shorter the unit, the more repeats it takes. Two
    guards keep Vietnamese forms safe: the loop must span >= 400 chars and
    the unit must carry letters or digits outside tags, because blank table
    rows and dot leaders ("Họ và tên: ......") repeat legitimately.
    """
    found = _find_loop(text)
    return found[0] if found else None


def assess_page(html: str, finish_reason: Optional[str] = None) -> Optional[str]:
    """Quality gate for one page of Chandra output; None means usable.

    Returns a key of PAGE_PROBLEM_LABELS. finish_reason "length" means the
    page hit max_tokens or its slot's context: the HTML is incomplete.
    """
    if finish_reason == "repetition":  # stream was stopped on a loop
        return "repetition"
    text = (html or "").strip()
    if not text:
        return "empty"
    if not _HTML_BLOCK_RE.search(text):
        return "no_html"
    if looks_like_reasoning(_top_level_text(text)):
        return "prose"
    if find_repetition_loop(text) is not None:
        return "repetition"
    if finish_reason == "length":
        return "truncated"
    return None


def salvage_page(html: str, problem: Optional[str]) -> str:
    """What may be stored and indexed from a page that failed the gate.

    Reasoning prose never survives: text outside the HTML blocks is
    removed, a loop is cut after its first instance, and a page with no
    HTML block left keeps nothing.
    """
    if problem is None:
        return html or ""
    if problem in ("error", "empty", "no_html") or not html:
        return ""
    text = html
    if problem == "repetition":
        found = _find_loop(text)
        if found:
            start, unit_len = found
            text = text[:start + unit_len]
    from bs4 import BeautifulSoup
    from bs4.element import Comment, NavigableString

    soup = BeautifulSoup(text, "html.parser")
    stray = [
        node for node in soup.contents
        if isinstance(node, NavigableString) and not isinstance(node, Comment)
        and node.strip()
    ]
    if stray:
        for node in stray:
            node.extract()
        text = str(soup)
    kept = text.strip()
    return kept if _HTML_BLOCK_RE.search(kept) else ""


def _image_to_base64(image: Union[Image.Image, str, bytes, np.ndarray]) -> str:
    """Convert any image format to base64 JPEG string.

    Performance fix 2026-04-18: switched PNG → JPEG 92% quality.
    Benchmark on 1247×1753 PDF page:
        PNG:     102ms encode · 834KB output
        JPEG 92:   5ms encode · 382KB output  ← 19.5x faster, 2.2x smaller

    Quality loss imperceptible for OCR (text is high-contrast);
    mmproj CLIP encoder on server resamples anyway.
    Saves ~97ms/page × N_pages before first token.
    """
    if isinstance(image, str):
        image = Image.open(image).convert('RGB')
    elif isinstance(image, bytes):
        image = Image.open(io.BytesIO(image)).convert('RGB')
    elif isinstance(image, np.ndarray):
        image = Image.fromarray(image).convert('RGB')
    elif image.mode != 'RGB':
        image = image.convert('RGB')

    buf = io.BytesIO()
    image.save(buf, format='JPEG', quality=92, optimize=False)
    return base64.b64encode(buf.getvalue()).decode()


def _clean_ocr_text(text: str) -> str:
    """Clean model output: remove markdown artifacts, normalize whitespace."""
    text = re.sub(r'\*\*([^*]+)\*\*', r'\1', text)  # bold
    text = re.sub(r'^#+\s*', '', text, flags=re.MULTILINE)  # headings
    text = re.sub(r'^\*\s+', '- ', text, flags=re.MULTILINE)  # bullets
    text = re.sub(r'\n{3,}', '\n\n', text)  # excessive newlines
    return text.strip()


class LlamaOCR:
    """OCR engine using llama-server (OpenAI-compatible API)."""

    # Compatibility with VinternOCR interface
    current_model_key = ACTIVE_OCR_MODEL

    def __init__(self, base_url: str = LLAMA_SERVER_URL):
        self.base_url = base_url.rstrip('/')
        self._client = httpx.Client(
            timeout=120,
            limits=httpx.Limits(max_connections=4, max_keepalive_connections=2),
        )
        atexit.register(self.close)
        logger.info(f"LlamaOCR initialized, server: {self.base_url}")

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def _payload(self, image, prompt: Optional[str],
                 max_tokens: Optional[int] = None,
                 repeat_penalty: Optional[float] = None,
                 stream: bool = False, retry: bool = False) -> dict:
        """Chat-completion request for one image, as every OCR call sends it."""
        if prompt is None:
            prompt = get_ocr_prompt()
        # Strip <image> tag if present — llama-server handles image separately
        prompt = prompt.replace('<image>\n', '').replace('<image>', '')
        b64 = _image_to_base64(image)

        # Build messages — include system prompt only if model needs it
        sys_prompt = get_ocr_system_prompt()
        messages = []
        if sys_prompt:
            messages.append({"role": "system", "content": sys_prompt})
        messages.append({"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
            {"type": "text", "text": prompt},
        ]})
        payload = {
            "messages": messages,
            "max_tokens": max_tokens or OCR_MAX_TOKENS,
            "temperature": app_config.OCR_RETRY_TEMPERATURE if retry else OCR_TEMPERATURE,
            "repeat_penalty": repeat_penalty if repeat_penalty is not None else OCR_REPEAT_PENALTY,
        }
        if retry:
            payload["top_p"] = app_config.OCR_RETRY_TOP_P
        payload.update(app_config.ocr_request_options())
        if stream:
            payload["stream"] = True
        return payload

    def _stream_chat(self, payload: dict, on_token=None,
                     stop_on_loop: bool = False) -> Tuple[str, Optional[str], int]:
        """POST a streaming chat completion.

        Returns (content, finish_reason, n_tokens). finish_reason is the
        server's ("stop", "length"), "cancelled" when on_token returned
        False, "repetition" when stop_on_loop cut a looping generation, or
        None when the stream ended without one. Raises OCRServerError on an
        SSE error event: the partial page must not pass for a whole one.
        """
        collected = []
        n_tokens = 0
        finish_reason = None
        with self._client.stream(
            "POST", f"{self.base_url}/v1/chat/completions",
            json=payload, timeout=180,
        ) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if not line or not line.startswith("data: "):
                    continue
                data_str = line[6:]  # strip "data: "
                if data_str.strip() == "[DONE]":
                    logger.debug("Stream: received [DONE]")
                    break
                try:
                    chunk = json.loads(data_str)
                except ValueError:
                    continue
                if not isinstance(chunk, dict):
                    continue
                if "error" in chunk:
                    error = chunk["error"]
                    message = error.get("message") if isinstance(error, dict) else str(error)
                    logger.error(f"Stream: server error after {n_tokens} tokens: {message}")
                    raise OCRServerError(message or "llama-server stream error")
                choice = (chunk.get("choices") or [{}])[0]
                content = (choice.get("delta") or {}).get("content") or ""
                if content:
                    collected.append(content)
                    n_tokens += 1
                    if on_token and n_tokens % 5 == 0:
                        # Cooperative cancellation: if on_token returns
                        # explicitly False, break stream early. Closing
                        # the httpx iterator sends FIN → server sees
                        # client disconnect → aborts slot + frees KV.
                        if on_token(n_tokens, content) is False:
                            logger.info(f"Stream aborted by on_token at {n_tokens} tokens")
                            finish_reason = "cancelled"
                            break
                    if (stop_on_loop and n_tokens % _LOOP_CHECK_EVERY == 0
                            and _find_loop("".join(collected)[-_LOOP_WINDOW:])):
                        logger.warning(f"Stream: repetition loop at {n_tokens} tokens, stopping")
                        finish_reason = "repetition"
                        break
                reason = choice.get("finish_reason")
                if reason:
                    finish_reason = reason
                    break
        logger.info(f"Stream: finish_reason={finish_reason}, {n_tokens} tokens")
        return "".join(collected), finish_reason, n_tokens

    def extract_text(self, image, prompt: Optional[str] = None,
                     timeout: Optional[float] = None,
                     max_tokens: Optional[int] = None,
                     repeat_penalty: Optional[float] = None) -> str:
        """
        Extract text from image using llama-server.

        Args:
            image: PIL.Image, file path, bytes, or numpy array
            prompt: OCR prompt (default: extract full document in markdown)
            timeout: Request timeout in seconds (default: client timeout)
            max_tokens: Override default max tokens (256 for regions, 4096 for full-page)
            repeat_penalty: Override default (3.5 for Vintern regions)

        Returns:
            str: Extracted text
        """
        try:
            start_time = time.time()
            payload = self._payload(image, prompt, max_tokens=max_tokens,
                                    repeat_penalty=repeat_penalty)
            response = self._client.post(
                f"{self.base_url}/v1/chat/completions",
                json=payload,
                timeout=timeout,
            )
            response.raise_for_status()

            data = response.json()
            choices = data.get("choices", [])
            if not choices:
                logger.error(f"Empty choices from server: {list(data.keys())}")
                return ""
            msg = choices[0].get("message", {})
            text = strip_reasoning(msg.get("content", ""))
            if not text:
                logger.warning("No content in server response")
                return ""
            text = _clean_ocr_text(text)

            elapsed = time.time() - start_time
            logger.info(f"OCR completed in {elapsed:.1f}s, {len(text)} chars")
            return text

        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error {e.response.status_code} from llama-server")
            return ""
        except httpx.ConnectError:
            logger.error(f"Cannot connect to llama-server at {self.base_url}")
            return ""
        except Exception as e:
            logger.error(f"OCR error: {e}")
            return ""

    def extract_text_from_region(self, image, region: tuple, prompt: Optional[str] = None) -> str:
        """Extract text from a specific region (x1, y1, x2, y2)."""
        if isinstance(image, str):
            image = Image.open(image).convert('RGB')
        elif isinstance(image, bytes):
            image = Image.open(io.BytesIO(image)).convert('RGB')
        elif isinstance(image, np.ndarray):
            image = Image.fromarray(image).convert('RGB')

        x1, y1, x2, y2 = region
        cropped = image.crop((x1, y1, x2, y2))
        return self.extract_text(cropped, prompt)

    def ocr_full_page_like_manual(self, image) -> str:
        """Full-page OCR — drop-in replacement for VinternOCR method."""
        return self.extract_text(image, get_ocr_prompt())

    def ocr_page(self, image, on_token=None, retry: bool = False) -> PageOCR:
        """Full-page OCR of one page, judged by the quality gate.

        Streams the generation (on_token gets progress and may cancel),
        stops early on a repetition loop and returns the HTML together
        with finish_reason and the gate's verdict (PageOCR.problem). With
        retry=True the request uses Chandra's retry sampling. Raises
        OCRServerError / httpx errors; the caller decides about retrying.
        """
        start_time = time.time()
        payload = self._payload(image, get_ocr_prompt(), stream=True, retry=retry)
        content, finish_reason, n_tokens = self._stream_chat(
            payload, on_token=on_token, stop_on_loop=True,
        )
        html = strip_reasoning(content)
        page = PageOCR(html=html, finish_reason=finish_reason, n_tokens=n_tokens)
        if finish_reason != "cancelled":
            page.problem = assess_page(html, finish_reason)
            if page.problem is None and finish_reason is None:
                # llama-server ends every finished generation with a
                # finish_reason; a stream that just stops was cut off.
                page.problem = "truncated"
        logger.info(
            f"Page OCR{' (retry)' if retry else ''} in {time.time() - start_time:.1f}s: "
            f"{n_tokens} tokens, {len(html)} chars, finish={finish_reason}, "
            f"problem={page.problem}"
        )
        if on_token:
            on_token(n_tokens, "")  # final callback
        return page

    def ocr_full_page_with_html(self, image, on_token=None) -> tuple:
        """Full-page OCR returning both cleaned text and raw HTML.

        Args:
            image: PIL Image or path
            on_token: Optional callback(n_tokens, chunk) called per streamed token
                      for real-time progress tracking.

        Returns:
            (cleaned_text, raw_html) — raw_html preserves <div>, <b>, <u> etc.
            for DOCX export and UI rendering.
        """
        if on_token:
            raw_html = self._extract_raw_streaming(image, get_ocr_prompt(), on_token=on_token)
        else:
            raw_html = self._extract_raw(image, get_ocr_prompt())
        cleaned = _clean_ocr_text(raw_html)
        return cleaned, raw_html

    def _extract_raw_streaming(self, image, prompt: str, on_token=None,
                                max_tokens: Optional[int] = None) -> str:
        """Like _extract_raw but streams tokens for real-time progress.

        Uses server-sent events (SSE) streaming from llama-server.
        Calls on_token(n_tokens, delta_text) after each token chunk.
        """
        try:
            start_time = time.time()
            payload = self._payload(image, prompt, max_tokens=max_tokens, stream=True)
            content, _finish, n_tokens = self._stream_chat(payload, on_token=on_token)
            text = strip_reasoning(content)

            elapsed = time.time() - start_time
            logger.info(f"Streaming OCR completed in {elapsed:.1f}s, {n_tokens} tokens, {len(text)} chars")
            if on_token:
                on_token(n_tokens, "")  # final callback
            return text

        except Exception as e:
            logger.error(f"Streaming OCR error: {e}")
            # Fallback to non-streaming
            logger.info("Falling back to non-streaming OCR")
            return self._extract_raw(image, prompt, max_tokens=max_tokens)

    def _extract_raw(self, image, prompt: str,
                     max_tokens: Optional[int] = None,
                     _retries: int = 2) -> str:
        """Like extract_text but returns raw HTML without _clean_ocr_text.

        Retries on timeout/connection errors (Chandra full-page can take 30-60s
        and GPU pressure from dual servers may cause transient failures).
        """
        payload = self._payload(image, prompt, max_tokens=max_tokens)

        for attempt in range(_retries + 1):
            try:
                start_time = time.time()
                response = self._client.post(
                    f"{self.base_url}/v1/chat/completions",
                    json=payload,
                    timeout=180,
                )
                response.raise_for_status()

                data = response.json()
                choices = data.get("choices", [])
                if not choices:
                    return ""
                content = choices[0].get("message", {}).get("content", "")
                text = strip_reasoning(content)

                elapsed = time.time() - start_time
                logger.info(
                    f"Raw OCR completed in {elapsed:.1f}s, {len(text)} chars, "
                    f"finish={choices[0].get('finish_reason')}"
                )
                return text

            except (httpx.ReadTimeout, httpx.ConnectError, httpx.RemoteProtocolError) as e:
                elapsed = time.time() - start_time
                if attempt < _retries:
                    logger.warning(f"Raw OCR attempt {attempt+1} failed after {elapsed:.1f}s: {e} — retrying...")
                    time.sleep(2)
                else:
                    logger.error(f"Raw OCR failed after {_retries+1} attempts: {e}")
                    return ""
            except Exception as e:
                logger.error(f"Raw OCR error: {e}")
                return ""
        return ""

    def extract_full_page_text(self, image) -> str:
        """Drop-in for VinternOCR.extract_full_page_text()."""
        return self.extract_text(image, get_ocr_prompt())

    def _ocr_region(self, image, config_params: Optional[dict] = None) -> str:
        """Drop-in for VinternOCR._ocr_region()."""
        class_id = (config_params or {}).get('class_id', -1)
        prompt = self.get_class_prompt(class_id)
        return self.extract_text(image, prompt)

    def get_class_prompt(self, class_id: int) -> str:
        """Get field-specific prompt by YOLO class ID (legacy — not used in Chandra pipeline)."""
        return get_ocr_prompt()

    def close(self):
        """Close HTTP client."""
        self._client.close()
