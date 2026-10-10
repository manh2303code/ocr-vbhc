import copy
import unittest

from agent_tools import ToolResult
from llm_client import ChatResponse, ToolCall
from rag_agent import AgentResult, Citation, DISCLAIMER_VI, RagAgent, SYSTEM_PROMPT_VI


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def chat(
        self,
        messages,
        tools=None,
        stream_cb=None,
        temperature=0.2,
        max_tokens=2048,
    ):
        del temperature, max_tokens
        self.calls.append({"messages": copy.deepcopy(messages), "tools": tools})
        response = self.responses.pop(0)
        if stream_cb and response.content:
            stream_cb(response.content)
        return response


class FakeExecutor:
    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    def execute(self, name, arguments):
        self.calls.append((name, arguments))
        return self.results.pop(0)


class PrefetchExecutor(FakeExecutor):
    def __init__(self, prefetch_result, results=()):
        super().__init__(results)
        self.prefetch_result = prefetch_result
        self.prefetch_questions = []

    def prefetch(self, question):
        self.prefetch_questions.append(question)
        return self.prefetch_result


def _call(arguments='{"query":"hộ chiếu"}', call_id="c1"):
    return ToolCall(call_id, "search_documents", arguments)


def _source(doc_id=7, page=3, chunk_id=11, number="01/2020/TT-BCA"):
    return {
        "doc_id": doc_id,
        "page": page,
        "chunk_id": chunk_id,
        "so_ki_hieu": number,
    }


class RagAgentTests(unittest.TestCase):
    def test_prefetch_always_grounds_first_answer_before_model_tool_filters(self):
        client = FakeClient(
            [ChatResponse("Văn bản phù hợp là Chỉ thị 02/CT-UBND [1].", [], "stop")]
        )
        executor = PrefetchExecutor(
            ToolResult("Chỉ thị về Tổng điều tra kinh tế", [_source(2, 1, 20, "02/CT-UBND")])
        )

        result = RagAgent(client, executor).ask("Văn bản nào về Tổng điều tra kinh tế?")

        self.assertEqual(executor.prefetch_questions, ["Văn bản nào về Tổng điều tra kinh tế?"])
        self.assertEqual([citation.doc_id for citation in result.citations], [2])
        context = "\n".join(
            message["content"] for message in client.calls[0]["messages"]
        )
        self.assertIn("[1]", context)
        self.assertIn("Chỉ thị về Tổng điều tra kinh tế", context)
    def test_tool_round_registers_citation_and_sends_numbered_tool_message(self):
        client = FakeClient(
            [
                ChatResponse("", [_call()], "tool_calls"),
                ChatResponse("Hộ chiếu được cấp theo [1].", [], "stop"),
            ]
        )
        executor = FakeExecutor([ToolResult("Quy định cấp hộ chiếu", [_source()])])

        result = RagAgent(client, executor).ask("Cấp hộ chiếu thế nào?")

        self.assertEqual(
            result,
            AgentResult(
                "Hộ chiếu được cấp theo [1].\n\n" + DISCLAIMER_VI,
                [Citation(1, 7, 3, "01/2020/TT-BCA", 11)],
                ["search_documents"],
            ),
        )
        self.assertEqual(executor.calls, [("search_documents", {"query": "hộ chiếu"})])
        tool_message = next(
            item for item in client.calls[1]["messages"] if item["role"] == "tool"
        )
        self.assertEqual(tool_message["tool_call_id"], "c1")
        self.assertIn("[1]", tool_message["content"])
        self.assertIn("Quy định cấp hộ chiếu", tool_message["content"])
        self.assertIn("NotebookLM", SYSTEM_PROMPT_VI)
        self.assertIn("Bối cảnh chung", SYSTEM_PROMPT_VI)

    def test_repeated_source_reuses_citation_number(self):
        repeated = _source()
        client = FakeClient(
            [
                ChatResponse("", [_call(call_id="c1")], "tool_calls"),
                ChatResponse("", [_call('{"query":"lại"}', "c2")], "tool_calls"),
                ChatResponse("Theo [1].", [], "stop"),
            ]
        )
        executor = FakeExecutor(
            [ToolResult("Lần một", [repeated]), ToolResult("Lần hai", [dict(repeated)])]
        )

        result = RagAgent(client, executor).ask("Hỏi")

        self.assertEqual([citation.n for citation in result.citations], [1])
        last_tool = [
            item for item in client.calls[2]["messages"] if item["role"] == "tool"
        ][-1]
        self.assertIn("[1]", last_tool["content"])
        self.assertNotIn("[2]", last_tool["content"])

    def test_each_search_block_gets_its_matching_citation_number(self):
        client = FakeClient(
            [
                ChatResponse("", [_call()], "tool_calls"),
                ChatResponse("Theo [1] và [2].", [], "stop"),
            ]
        )
        executor = FakeExecutor(
            [
                ToolResult(
                    "block-one\n\nblock-two",
                    [_source(), _source(8, 4, 12, "02/2024/NĐ-CP")],
                )
            ]
        )

        RagAgent(client, executor).ask("Hỏi")

        tool_message = next(
            item for item in client.calls[1]["messages"] if item["role"] == "tool"
        )
        self.assertIn("[1] block-one", tool_message["content"])
        self.assertIn("[2] block-two", tool_message["content"])

    def test_round_budget_forces_final_call_without_tools(self):
        client = FakeClient(
            [
                ChatResponse("", [_call()], "tool_calls"),
                ChatResponse("Trả lời từ dữ liệu [1].", [], "stop"),
            ]
        )
        executor = FakeExecutor([ToolResult("Dữ liệu", [_source()])])

        result = RagAgent(client, executor, max_rounds=1).ask("Hỏi")

        self.assertIsNotNone(client.calls[0]["tools"])
        self.assertIsNone(client.calls[1]["tools"])
        self.assertEqual(
            client.calls[1]["messages"][-1],
            {"role": "user", "content": "Hãy trả lời ngay bằng dữ liệu đã có."},
        )
        self.assertIn("[1]", result.answer)

    def test_cancel_before_start_does_not_call_client(self):
        client = FakeClient([])

        result = RagAgent(client, FakeExecutor([]), cancel_check=lambda: True).ask("Hỏi")

        self.assertEqual(result.error, "cancelled")
        self.assertEqual(client.calls, [])

    def test_cancel_between_rounds_stops_before_second_client_call(self):
        checks = iter([False, False, False, True])
        client = FakeClient([ChatResponse("", [_call()], "tool_calls")])
        executor = FakeExecutor([ToolResult("Dữ liệu", [_source()])])

        result = RagAgent(
            client, executor, cancel_check=lambda: next(checks)
        ).ask("Hỏi")

        self.assertEqual(result.error, "cancelled")
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(len(executor.calls), 1)

    def test_malformed_json_gets_two_retries_then_forced_answer(self):
        malformed = ChatResponse("", [_call("not-json")], "tool_calls")
        client = FakeClient(
            [
                ChatResponse("", [_call(call_id="valid")], "tool_calls"),
                malformed,
                malformed,
                malformed,
                ChatResponse("Trả lời bằng căn cứ [1].", [], "stop"),
            ]
        )

        executor = FakeExecutor([ToolResult("Dữ liệu đã tìm được", [_source()])])
        result = RagAgent(client, executor, max_rounds=6).ask("Hỏi")

        self.assertEqual(len(client.calls), 5)
        self.assertEqual(client.calls[-1]["tools"], None)
        all_messages = client.calls[-1]["messages"]
        errors = [
            item for item in all_messages
            if item["role"] == "tool" and "không phải JSON hợp lệ" in item["content"]
        ]
        self.assertEqual(len(errors), 2)
        self.assertEqual(len(executor.calls), 1)
        self.assertEqual(result.citations, [Citation(1, 7, 3, "01/2020/TT-BCA", 11)])
        self.assertIn("[1]", result.answer)
        self.assertTrue(result.answer.endswith(DISCLAIMER_VI))

    def test_multiple_malformed_calls_never_exceed_two_error_feedbacks(self):
        calls = [_call("bad", "bad{}".format(index)) for index in range(3)]
        client = FakeClient(
            [
                ChatResponse("", calls, "tool_calls"),
                ChatResponse("Không đủ căn cứ.", [], "stop"),
            ]
        )

        RagAgent(client, FakeExecutor([])).ask("Hỏi")

        self.assertEqual(len(client.calls), 2)
        self.assertIsNone(client.calls[-1]["tools"])
        self.assertFalse(
            any(message["role"] == "tool" for message in client.calls[-1]["messages"])
        )

    def test_history_is_not_mutated_and_disclaimer_is_not_duplicated(self):
        history = [{"role": "assistant", "content": "Câu trước", "meta": {"x": 1}}]
        original = copy.deepcopy(history)
        client = FakeClient(
            [ChatResponse("Kết quả.\n\n" + DISCLAIMER_VI, [], "stop")]
        )

        result = RagAgent(client, FakeExecutor([])).ask("Câu mới", history)

        self.assertEqual(history, original)
        self.assertEqual(result.answer.count(DISCLAIMER_VI), 1)
        self.assertTrue(result.answer.endswith(DISCLAIMER_VI))
        self.assertEqual(client.calls[0]["messages"][1], history[0])

    def test_callbacks_receive_steps_and_streamed_text(self):
        steps, tokens = [], []
        client = FakeClient(
            [
                ChatResponse("", [_call()], "tool_calls"),
                ChatResponse("Câu trả lời", [], "stop"),
            ]
        )
        executor = FakeExecutor([ToolResult("Dữ liệu", [])])

        RagAgent(client, executor, step_cb=steps.append, token_cb=tokens.append).ask("Hỏi")

        self.assertEqual(steps, ["search_documents"])
        self.assertEqual(tokens, ["Câu trả lời"])

    def test_final_answer_strips_thinking_and_adds_missing_registered_sources(self):
        client = FakeClient(
            [
                ChatResponse("", [_call()], "tool_calls"),
                ChatResponse(
                    "<think>phân tích nội bộ\nkhông được hiển thị</think>"
                    "Nội dung trả lời từ tài liệu.",
                    [],
                    "stop",
                ),
            ]
        )
        executor = FakeExecutor(
            [
                ToolResult(
                    "nguồn một\n\nnguồn hai",
                    [_source(), _source(8, 4, 12, "02/2024/NĐ-CP")],
                )
            ]
        )

        result = RagAgent(client, executor).ask("Hỏi")

        self.assertNotIn("<think>", result.answer)
        self.assertNotIn("phân tích nội bộ", result.answer)
        self.assertIn("Nội dung trả lời từ tài liệu.\n\nNguồn: [1] [2]", result.answer)
        self.assertTrue(result.answer.endswith(DISCLAIMER_VI))

    def test_unclosed_reasoning_and_unregistered_citations_are_removed(self):
        result = RagAgent(
            FakeClient([ChatResponse("Kết luận [99].<think>nội bộ", [], "stop")]),
            FakeExecutor([]),
        ).ask("Hỏi")

        self.assertNotIn("nội bộ", result.answer)
        self.assertNotIn("[99]", result.answer)

    def test_existing_citation_is_not_duplicated_with_source_footer(self):
        client = FakeClient(
            [
                ChatResponse("", [_call()], "tool_calls"),
                ChatResponse("Theo quy định tại [1].", [], "stop"),
            ]
        )
        result = RagAgent(
            client, FakeExecutor([ToolResult("nguồn", [_source()])])
        ).ask("Hỏi")

        self.assertNotIn("Nguồn:", result.answer)
        self.assertEqual(result.answer.count("[1]"), 1)

    def test_answer_without_registered_citations_is_unchanged(self):
        result = RagAgent(
            FakeClient([ChatResponse("Không có căn cứ trong kho.", [], "stop")]),
            FakeExecutor([]),
        ).ask("Hỏi")

        self.assertEqual(
            result.answer,
            "Không có căn cứ trong kho.\n\n" + DISCLAIMER_VI,
        )
if __name__ == "__main__":
    unittest.main()
