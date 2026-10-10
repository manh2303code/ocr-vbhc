"""Small, framework-free agent loop for the local legal RAG tools."""

import copy
import json
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from agent_tools import TOOLS_SCHEMA, ToolResult


DISCLAIMER_VI = "⚠️ Thông tin tra cứu tự động, không thay thế tư vấn pháp lý."
SYSTEM_PROMPT_VI = """Bạn là trợ lý nghiên cứu văn bản hành chính theo phong cách NotebookLM.
Ưu tiên trả lời trực tiếp, có cấu trúc ngắn gọn, tổng hợp đúng trọng tâm từ NGUỒN TRONG KHO.
Mọi nhận định về số hiệu, nội dung, cơ quan, ngày tháng hoặc hiệu lực văn bản phải dựa trên
nguồn và trích dẫn đúng [n]. Không được tự thêm bộ lọc cơ quan/ngày/loại nếu người dùng không
nêu rõ. Có thể dùng kiến thức đã học sẵn để giải thích bối cảnh chung, nhưng phải đặt riêng dưới
nhãn "Bối cảnh chung", không biến kiến thức chung thành nội dung của văn bản và không tạo citation.
Nếu nguồn không đủ thì nói rõ phần nào chưa xác minh được. Không hiển thị reasoning, thinking,
tên công cụ hay quy trình nội bộ. Luôn cảnh báo khi văn bản liên quan đã hết hiệu lực.
Mọi câu trả lời phải kết thúc bằng: {}""".format(DISCLAIMER_VI)


@dataclass(frozen=True)
class Citation:
    n: int
    doc_id: int
    page: Optional[int]
    so_ki_hieu: str
    chunk_id: Optional[int]


@dataclass
class AgentResult:
    answer: str
    citations: List[Citation]
    steps: List[str]
    error: Optional[str] = None


class RagAgent:
    def __init__(
        self,
        client,
        executor,
        max_rounds: int = 6,
        step_cb=None,
        token_cb=None,
        cancel_check=None,
    ):
        if max_rounds < 1:
            raise ValueError("max_rounds phải lớn hơn 0")
        self.client = client
        self.executor = executor
        self.max_rounds = max_rounds
        self.step_cb = step_cb
        self.token_cb = token_cb
        self.cancel_check = cancel_check or (lambda: False)

    @staticmethod
    def _assistant_message(response):
        message = {"role": "assistant", "content": response.content or ""}
        message["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {"name": call.name, "arguments": call.arguments},
            }
            for call in response.tool_calls
        ]
        return message

    @staticmethod
    def _source_key(source: dict) -> Tuple:
        chunk_id = source.get("chunk_id")
        if chunk_id is not None:
            return ("chunk", int(source["doc_id"]), int(chunk_id))
        return ("document", int(source["doc_id"]), source.get("page"))

    @staticmethod
    def _citation(source: dict, n: int) -> Citation:
        return Citation(
            n=n,
            doc_id=int(source["doc_id"]),
            page=int(source["page"]) if source.get("page") is not None else None,
            so_ki_hieu=str(source.get("so_ki_hieu") or ""),
            chunk_id=(
                int(source["chunk_id"])
                if source.get("chunk_id") is not None
                else None
            ),
        )

    @staticmethod
    def _format_tool_result(result: ToolResult, numbers: List[int]) -> str:
        blocks = result.text.split("\n\n")
        if numbers and len(blocks) == len(numbers):
            return "\n\n".join(
                "[{}] {}".format(number, block) for number, block in zip(numbers, blocks)
            )
        prefix = " ".join("[{}]".format(number) for number in numbers)
        return "{} {}".format(prefix, result.text).strip()

    @staticmethod
    def _final_answer(content: str, citations: List[Citation]) -> str:
        visible = re.sub(
            r"<think\b[^>]*>.*?</think\s*>",
            "",
            content or "",
            flags=re.IGNORECASE | re.DOTALL,
        )
        visible = re.sub(
            r"<(?:think|analysis|reasoning)\b[^>]*>.*$",
            "",
            visible,
            flags=re.IGNORECASE | re.DOTALL,
        )
        visible = re.sub(
            r"\[(?:n|nguồn)\s*(\d+)\]", r"[\1]", visible,
            flags=re.IGNORECASE,
        )
        visible = re.sub(r"\[n\]", "", visible, flags=re.IGNORECASE)
        valid_numbers = {citation.n for citation in citations}
        visible = re.sub(
            r"\[(\d+)\]",
            lambda match: match.group(0) if int(match.group(1)) in valid_numbers else "",
            visible,
        )
        answer = visible.replace(DISCLAIMER_VI, "").strip()
        if not answer:
            answer = "Không tìm thấy đủ căn cứ trong kho tài liệu."
        if citations and not re.search(r"\[\d+\]", answer):
            numbers = list(dict.fromkeys(citation.n for citation in citations))[:4]
            answer += "\n\nNguồn: " + " ".join(
                "[{}]".format(number) for number in numbers
            )
        return "{}\n\n{}".format(answer, DISCLAIMER_VI)

    def ask(self, question: str, history: Optional[List[dict]] = None) -> AgentResult:
        messages = [{"role": "system", "content": SYSTEM_PROMPT_VI}]
        messages.extend(copy.deepcopy(history or []))
        citations = []  # type: List[Citation]
        registry = {}  # type: Dict[Tuple, int]
        steps = []  # type: List[str]
        malformed_feedbacks = 0
        tool_rounds = 0

        def register(result):
            numbers = []
            for source in result.sources:
                key = self._source_key(source)
                if key not in registry:
                    registry[key] = len(citations) + 1
                    citations.append(self._citation(source, registry[key]))
                numbers.append(registry[key])
            return numbers

        prefetch = getattr(self.executor, "prefetch", None)
        if callable(prefetch) and not self.cancel_check():
            result = prefetch(question)
            numbers = register(result)
            steps.append("prefetch_documents")
            if self.step_cb:
                self.step_cb("prefetch_documents")
            messages.append(
                {
                    "role": "system",
                    "content": "NGUỒN TRONG KHO (truy xuất tự động, phải ưu tiên):\n{}".format(
                        self._format_tool_result(result, numbers)
                    ),
                }
            )
        messages.append({"role": "user", "content": question})

        def cancelled():
            return AgentResult("", list(citations), list(steps), "cancelled")

        def completed(content):
            answer = self._final_answer(content, citations)
            referenced = {int(number) for number in re.findall(r"\[(\d+)\]", answer)}
            used = [citation for citation in citations if citation.n in referenced]
            return AgentResult(answer, used, list(steps))

        def finish_now():
            messages.append(
                {"role": "user", "content": "Hãy trả lời ngay bằng dữ liệu đã có."}
            )
            if self.cancel_check():
                return cancelled()
            response = self.client.chat(messages, tools=None, stream_cb=self.token_cb)
            if self.cancel_check():
                return cancelled()
            return completed(response.content)

        while True:
            if self.cancel_check():
                return cancelled()
            response = self.client.chat(
                messages, tools=TOOLS_SCHEMA, stream_cb=self.token_cb
            )
            if self.cancel_check():
                return cancelled()
            if not response.tool_calls:
                return completed(response.content)

            parsed = []
            malformed = []
            for call in response.tool_calls:
                try:
                    arguments = json.loads(call.arguments)
                    if not isinstance(arguments, dict):
                        raise ValueError
                    parsed.append((call, arguments))
                except (json.JSONDecodeError, TypeError, ValueError):
                    malformed.append(call)

            if malformed and malformed_feedbacks + len(malformed) > 2:
                return finish_now()

            messages.append(self._assistant_message(response))
            for call, arguments in parsed:
                if self.cancel_check():
                    return cancelled()
                steps.append(call.name)
                if self.step_cb:
                    self.step_cb(call.name)
                result = self.executor.execute(call.name, arguments)
                numbers = register(result)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": self._format_tool_result(result, numbers),
                    }
                )
            for call in malformed:
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": "Lỗi: arguments không phải JSON hợp lệ, thử lại",
                    }
                )
                malformed_feedbacks += 1

            tool_rounds += 1
            if tool_rounds >= self.max_rounds:
                return finish_now()
