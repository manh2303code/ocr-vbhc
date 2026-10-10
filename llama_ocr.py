"""
OCR engine using llama-server HTTP API (OpenAI-compatible).
Drop-in replacement for VinternOCR — same extract_text() interface.
"""
import io
import atexit
import re
import base64
import logging
import time
from typing import Optional, Union

import httpx
import numpy as np
from PIL import Image

from app_config import (
    ACTIVE_OCR_MODEL, LLAMA_SERVER_URL, OCR_MAX_TOKENS, OCR_TEMPERATURE,
    OCR_REPEAT_PENALTY,
    get_ocr_prompt, get_ocr_system_prompt,
)

logger = logging.getLogger("LlamaOCR")

# NOTE: Per-field CLASS_PROMPTS removed — Chandra-only pipeline parses HTML
# instead of per-region OCR. See chandra_field_parser.py.


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
        if prompt is None:
            prompt = get_ocr_prompt()

        # Strip <image> tag if present — llama-server handles image separately
        prompt = prompt.replace('<image>\n', '').replace('<image>', '')

        try:
            start_time = time.time()
            b64 = _image_to_base64(image)
            logger.info(f"Image encoded ({len(b64) // 1024}KB), prompt: {prompt[:50]}...")

            # Build messages — include system prompt only if model needs it
            sys_prompt = get_ocr_system_prompt()
            messages = []
            if sys_prompt:
                messages.append({"role": "system", "content": sys_prompt})
            messages.append({"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                {"type": "text", "text": prompt},
            ]})

            response = self._client.post(
                f"{self.base_url}/v1/chat/completions",
                json={
                    "messages": messages,
                    "max_tokens": max_tokens or OCR_MAX_TOKENS,
                    "temperature": OCR_TEMPERATURE,
                    "repeat_penalty": repeat_penalty if repeat_penalty is not None else OCR_REPEAT_PENALTY,
                },
                timeout=timeout,
            )
            response.raise_for_status()

            data = response.json()
            choices = data.get("choices", [])
            if not choices:
                logger.error(f"Empty choices from server: {list(data.keys())}")
                return ""
            msg = choices[0].get("message", {})
            content = msg.get("content", "")

            # With --reasoning-format none, full output is in content.
            # Chandra (Qwen 3.5) may embed <think>...</think> in content.
            # Three cases observed:
            #   1) <think><div ...>OCR</div>... (no closing) — direct OCR after tag
            #   2) <think>English thinking...\n<div>OCR</div>... (no closing) — thinking + OCR mixed
            #   3) <think>thinking</think><div>OCR</div>... — clean split
            # Strategy: strip thinking, keep only HTML OCR content.
            if "<think>" in content:
                m = re.search(r'<think>(.*?)</think>(.*)', content, flags=re.DOTALL)
                if m:
                    thinking_text = m.group(1).strip()
                    after_text = m.group(2).strip()
                    # OCR HTML lives AFTER </think>. Thinking is English analysis
                    # prose (no <div>). Prefer after_text when it has <div>;
                    # fall back to thinking only if after lacks HTML (rare).
                    if "<div" in after_text:
                        text = after_text
                        picked = "after"
                    elif "<div" in thinking_text:
                        text = thinking_text
                        picked = "thinking(fallback)"
                    else:
                        text = after_text or thinking_text
                        picked = "neither(no<div>)"
                    logger.info(
                        f"<think> closing: think={len(thinking_text)}, "
                        f"after={len(after_text)}, picked={picked}"
                    )
                else:
                    # No </think> — strip <think> tag, keep rest
                    text = content.replace("<think>", "", 1).strip()
                    logger.info(f"<think> without closing: {len(text)} chars (stripped tag)")

                # Strip any non-HTML preamble before first <div
                if "<div" in text:
                    div_pos = text.index("<div")
                    if div_pos > 0:
                        preamble = text[:div_pos].strip()
                        text = text[div_pos:]
                        if preamble:
                            logger.info(f"Stripped {len(preamble)} chars of preamble before <div>")
            else:
                # Vintern / clean output — no thinking tags
                text = content
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
        if prompt is None:
            prompt = get_ocr_prompt()
        prompt = prompt.replace('<image>\n', '').replace('<image>', '')

        b64 = _image_to_base64(image)

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
            "temperature": OCR_TEMPERATURE,
            "repeat_penalty": OCR_REPEAT_PENALTY,
            "stream": True,
        }

        try:
            start_time = time.time()
            collected = []
            n_tokens = 0

            import json as _json

            with self._client.stream(
                "POST", f"{self.base_url}/v1/chat/completions",
                json=payload, timeout=180,
            ) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if not line:
                        continue
                    if not line.startswith("data: "):
                        continue
                    data_str = line[6:]  # strip "data: "
                    if data_str.strip() == "[DONE]":
                        logger.debug("Stream: received [DONE]")
                        break
                    try:
                        chunk = _json.loads(data_str)
                        choice = chunk.get("choices", [{}])[0]
                        delta = choice.get("delta", {})
                        content = delta.get("content", "")
                        finish_reason = choice.get("finish_reason")

                        if content:
                            collected.append(content)
                            n_tokens += 1
                            if on_token and n_tokens % 5 == 0:
                                # Cooperative cancellation: if on_token returns
                                # explicitly False, break stream early. Closing
                                # the httpx iterator sends FIN → server sees
                                # client disconnect → aborts slot + frees KV.
                                # 2026-04-18 fix: previously ignored return
                                # value, mid-page cancel was impossible.
                                result = on_token(n_tokens, content)
                                if result is False:
                                    logger.info(
                                        f"Stream aborted by on_token at {n_tokens} tokens"
                                    )
                                    break

                        # Stop on finish_reason (length, stop, etc.)
                        if finish_reason:
                            logger.info(f"Stream: finish_reason={finish_reason}, {n_tokens} tokens")
                            break
                    except (ValueError, IndexError, KeyError):
                        continue

            content = "".join(collected)

            # Strip <think> — prefer HTML after </think>, fall back to thinking
            # only if after lacks <div> (rare Chandra edge case).
            if "<think>" in content:
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
                    logger.info(
                        f"[stream] <think>: think={len(thinking_text)}, "
                        f"after={len(after_text)}, picked={'after' if text == after_text else 'thinking/empty'}"
                    )
                else:
                    text = content.replace("<think>", "", 1).strip()

                if "<div" in text:
                    div_pos = text.index("<div")
                    if div_pos > 0:
                        text = text[div_pos:]
            else:
                text = content

            elapsed = time.time() - start_time
            logger.info(f"Streaming OCR completed in {elapsed:.1f}s, {n_tokens} tokens, {len(text)} chars")
            if on_token:
                on_token(n_tokens, "")  # final callback
            return text.strip()

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
        if prompt is None:
            prompt = get_ocr_prompt()
        prompt = prompt.replace('<image>\n', '').replace('<image>', '')

        b64 = _image_to_base64(image)

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
            "temperature": OCR_TEMPERATURE,
            "repeat_penalty": OCR_REPEAT_PENALTY,
        }

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

                # Strip <think> — prefer HTML after </think>; thinking is reasoning.
                if "<think>" in content:
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
                            text = text[div_pos:]
                else:
                    text = content

                elapsed = time.time() - start_time
                logger.info(f"Raw OCR completed in {elapsed:.1f}s, {len(text)} chars")
                return text.strip()

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
