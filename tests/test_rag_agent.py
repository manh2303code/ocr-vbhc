import copy
import unittest

from agent_tools import ToolResult
from llm_client import ChatResponse, ContextOverflowError, ToolCall, estimate_prompt_tokens
from rag_agent import (
    AgentResult,
    CONTEXT_EXCEEDED_VI,
    Citation,
    DISCLAIMER_VI,
    RagAgent,
    SYSTEM_PROMPT_VI,
    TRUNCATED_NOTICE_VI,
    answer_reserve,
    bounded_history,
    max_rounds_for,
    prompt_budget,
    strip_source_footer,
)


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

    def test_sources_cb_reports_every_newly_registered_source_in_order(self):
        """The panel shows which sources are being read before the answer
        arrives; it needs the numbered list as soon as each tool registers it."""
        seen = []
        client = FakeClient(
            [
                ChatResponse("", [_call()], "tool_calls"),
                ChatResponse("Trả lời [1] [2]", [], "stop"),
            ]
        )
        executor = PrefetchExecutor(
            ToolResult("đoạn một", [_source(2, 1, 20, "02/CT-UBND")]),
            [ToolResult("đoạn hai\n\nđoạn một", [
                _source(3, 4, 30, "03/QĐ-UBND"), _source(2, 1, 20, "02/CT-UBND"),
            ])],
        )

        RagAgent(client, executor, sources_cb=seen.append).ask("Hỏi")

        self.assertEqual(
            [[(1, 2)], [(1, 2), (2, 3)]],
            [[(c.n, c.doc_id) for c in batch] for batch in seen],
        )

    def test_sources_cb_is_quiet_when_a_tool_adds_nothing_new(self):
        seen = []
        client = FakeClient(
            [
                ChatResponse("", [_call()], "tool_calls"),
                ChatResponse("Trả lời", [], "stop"),
            ]
        )
        executor = FakeExecutor([ToolResult("Không tìm thấy.", [])])

        RagAgent(client, executor, sources_cb=seen.append).ask("Hỏi")

        self.assertEqual([], seen)

    def test_final_answer_strips_thinking_and_credits_the_supporting_sources(self):
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
                    "Nội dung trả lời nằm trong tài liệu thứ nhất\n\n"
                    "Tài liệu thứ hai cũng nêu nội dung trả lời",
                    [_source(), _source(8, 4, 12, "02/2024/NĐ-CP")],
                )
            ]
        )

        result = RagAgent(client, executor).ask("Hỏi")

        self.assertNotIn("<think>", result.answer)
        self.assertNotIn("phân tích nội bộ", result.answer)
        # The sources are shown above the answer, not listed under it.
        self.assertEqual("Nội dung trả lời từ tài liệu.\n\n" + DISCLAIMER_VI, result.answer)
        self.assertEqual([1, 2], [citation.n for citation in result.citations])

    def test_unclosed_reasoning_and_unregistered_citations_are_removed(self):
        result = RagAgent(
            FakeClient([ChatResponse("Kết luận [99].<think>nội bộ", [], "stop")]),
            FakeExecutor([]),
        ).ask("Hỏi")

        self.assertNotIn("nội bộ", result.answer)
        self.assertNotIn("[99]", result.answer)

    def test_inline_citation_stays_once_and_is_the_answers_source(self):
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
        self.assertEqual([1], [citation.n for citation in result.citations])

    def test_inline_citations_decide_the_sources_over_the_supported_ones(self):
        client = FakeClient(
            [
                ChatResponse("", [_call()], "tool_calls"),
                ChatResponse("Hộ chiếu ngoại giao có thời hạn 5 năm [2].", [], "stop"),
            ]
        )
        result = RagAgent(client, FakeExecutor([ToolResult(
            "Hộ chiếu ngoại giao có thời hạn 5 năm\n\nHộ chiếu ngoại giao có thời hạn 5 năm",
            [_source(), _source(8, 4, 12, "02/2024/NĐ-CP")],
        )])).ask("Hỏi")

        self.assertEqual([2], [citation.n for citation in result.citations])

    def test_not_found_answer_rests_on_no_source(self):
        client = FakeClient(
            [
                ChatResponse("", [_call()], "tool_calls"),
                ChatResponse("Không tìm thấy thông tin này trong kho tài liệu.", [], "stop"),
            ]
        )
        result = RagAgent(
            client, FakeExecutor([ToolResult("nguồn", [_source()])])
        ).ask("Hỏi")

        self.assertNotIn("Nguồn:", result.answer)
        self.assertEqual(result.citations, [])

    def test_label_bracket_citation_is_read_as_a_citation(self):
        client = FakeClient(
            [
                ChatResponse("", [_call()], "tool_calls"),
                ChatResponse("Hộ chiếu có thời hạn 5 năm 【1】.", [], "stop"),
            ]
        )
        result = RagAgent(
            client, FakeExecutor([ToolResult("nguồn", [_source()])])
        ).ask("Hỏi")

        self.assertIn("5 năm [1].", result.answer)
        self.assertNotIn("Nguồn:", result.answer)
        self.assertEqual([citation.n for citation in result.citations], [1])

    def test_partial_answer_keeps_its_supporting_source(self):
        client = FakeClient(
            [
                ChatResponse("", [_call()], "tool_calls"),
                ChatResponse("Hộ chiếu có thời hạn 5 năm. Không tìm thấy quy định về lệ phí.", [], "stop"),
            ]
        )
        result = RagAgent(
            client,
            FakeExecutor([ToolResult("Hộ chiếu ngoại giao có thời hạn 5 năm kể từ ngày cấp", [_source()])]),
        ).ask("Hỏi")

        self.assertEqual([1], [citation.n for citation in result.citations])
        self.assertNotIn("Nguồn:", result.answer)

    def test_at_most_four_supporting_sources_are_credited(self):
        text = "Hộ chiếu ngoại giao có thời hạn 5 năm kể từ ngày cấp"
        client = FakeClient(
            [
                ChatResponse("", [_call()], "tool_calls"),
                ChatResponse("Hộ chiếu ngoại giao có thời hạn 5 năm.", [], "stop"),
            ]
        )
        sources = [_source(doc, 1, 100 + doc, "{}/QĐ".format(doc)) for doc in range(1, 7)]
        result = RagAgent(client, FakeExecutor([ToolResult(
            "\n\n".join([text] * 6), sources,
        )])).ask("Hỏi")

        self.assertEqual([1, 2, 3, 4], [citation.n for citation in result.citations])
        self.assertNotIn("Nguồn:", result.answer)

    def test_final_answer_text_never_lists_its_sources(self):
        # Kept for replays that call it directly: the text only, no footer.
        answer = RagAgent._final_answer(
            "Hộ chiếu có thời hạn 5 năm.", [Citation(1, 7, 3, "01/2020/TT-BCA", 11)]
        )

        self.assertEqual("Hộ chiếu có thời hạn 5 năm.\n\n" + DISCLAIMER_VI, answer)

    def test_answer_without_registered_citations_is_unchanged(self):
        result = RagAgent(
            FakeClient([ChatResponse("Không có căn cứ trong kho.", [], "stop")]),
            FakeExecutor([]),
        ).ask("Hỏi")

        self.assertEqual(
            result.answer,
            "Không có căn cứ trong kho.\n\n" + DISCLAIMER_VI,
        )


class BudgetClient(FakeClient):
    """Counts prompts like the estimate does and, like llama-server, refuses
    the first `overflows` requests as larger than the context."""

    def __init__(self, responses, n_ctx=None, overflows=0, server_n_ctx=4096):
        super().__init__(responses)
        self.n_ctx = n_ctx
        self.overflows = overflows
        self.server_n_ctx = server_n_ctx
        self.max_tokens = []
        self.prompts = []
        self.context_probes = 0

    def context_size(self):
        self.context_probes += 1
        return self.n_ctx

    def count_prompt_tokens(self, messages, tools=None):
        return estimate_prompt_tokens(messages, tools)

    def chat(self, messages, tools=None, stream_cb=None, temperature=0.2, max_tokens=2048):
        prompt = estimate_prompt_tokens(messages, tools)
        if self.overflows:
            self.overflows -= 1
            raise ContextOverflowError(prompt, self.server_n_ctx, "exceeds the available context size")
        self.max_tokens.append(max_tokens)
        self.prompts.append(prompt)
        return super().chat(messages, tools, stream_cb)


def _blocks(count, size, first=1):
    return "\n\n".join(
        "【{}/QĐ · Quyết định · trang 1】\n{}".format(first + index, "nội dung " * (size // 9))
        for index in range(count)
    )


def _sources(count, first=1):
    return [_source(first + index, 1, 100 + first + index, "{}/QĐ".format(first + index))
            for index in range(count)]


class TokenBudgetTests(unittest.TestCase):
    """research/token_budget_audit.md §3.9: every request leaves room for
    the answer inside n_ctx, and an over-long prompt is trimmed, not sent."""

    def test_reserve_and_budget_follow_the_context(self):
        self.assertEqual(answer_reserve(8192), 1024)
        self.assertEqual(answer_reserve(16384), 2048)
        self.assertEqual(answer_reserve(32768), 2048)
        self.assertEqual(prompt_budget(32768), 30464)
        self.assertEqual(prompt_budget(24576), 22272)
        self.assertEqual(prompt_budget(16384), 14080)
        self.assertEqual(prompt_budget(8192), 6912)

    def test_rounds_are_eight_on_gpu_and_four_on_cpu(self):
        self.assertEqual(max_rounds_for("cuda"), 8)
        self.assertEqual(max_rounds_for("cpu"), 4)

    def test_max_tokens_is_2048_when_the_prompt_leaves_room(self):
        # The CPU reserve (1024) only bounds the prompt; an answer may use
        # whatever the context has left, up to 2048.
        for n_ctx in (8192, 32768):
            client = BudgetClient([ChatResponse("Trả lời.", [], "stop")], n_ctx=n_ctx)
            RagAgent(client, FakeExecutor([])).ask("Hỏi")
            self.assertEqual(client.max_tokens, [2048])

    def test_max_tokens_is_what_the_context_has_left_after_a_long_prompt(self):
        executor = PrefetchExecutor(ToolResult(_blocks(3, 4000), _sources(3)))
        client = BudgetClient([ChatResponse("Đáp [1].", [], "stop")], n_ctx=8192)

        RagAgent(client, executor).ask("Hỏi")

        self.assertGreater(client.prompts[0], 8192 - 2048 - 64)
        self.assertLessEqual(client.prompts[0], prompt_budget(8192))
        self.assertEqual(client.max_tokens, [8192 - client.prompts[0] - 64])

    def test_context_size_is_asked_once_and_given_size_wins(self):
        client = BudgetClient(
            [ChatResponse("", [_call()], "tool_calls"), ChatResponse("Xong.", [], "stop")],
            n_ctx=8192,
        )
        RagAgent(client, FakeExecutor([ToolResult("Dữ liệu", [_source()])])).ask("Hỏi")
        self.assertEqual(client.context_probes, 1)

        client = BudgetClient([ChatResponse("Xong.", [], "stop")], n_ctx=8192)
        RagAgent(client, FakeExecutor([]), n_ctx=32768).ask("Hỏi")
        self.assertEqual(client.context_probes, 0)
        self.assertEqual(client.max_tokens, [2048])

    def test_older_tool_results_shrink_first_and_keep_their_labels(self):
        client = BudgetClient(
            [
                ChatResponse("", [_call(call_id="c1")], "tool_calls"),
                ChatResponse("", [_call('{"query":"thêm"}', "c2")], "tool_calls"),
                ChatResponse("Kết luận [4].", [], "stop"),
            ],
            n_ctx=8192,
        )
        executor = FakeExecutor([
            ToolResult(_blocks(3, 3000), _sources(3)),
            ToolResult(_blocks(3, 3000, first=4), _sources(3, first=4)),
        ])

        result = RagAgent(client, executor, max_rounds=3).ask("Hỏi")

        last = client.calls[-1]["messages"]
        tools = [message for message in last if message["role"] == "tool"]
        self.assertLess(len(tools[0]["content"]), 2000)
        for number in (1, 2, 3):
            self.assertIn("[{}] 【{}/QĐ".format(number, number), tools[0]["content"])
        self.assertIn("nội dung " * 300, tools[1]["content"])
        self.assertLessEqual(client.prompts[-1], prompt_budget(8192))
        self.assertIsNone(result.error)
        self.assertIn("[4]", result.answer)

    def test_history_goes_before_the_newest_sources_and_question_stays(self):
        history = []
        for index in range(6):
            history.append({"role": "user", "content": "câu hỏi {} ".format(index) * 20})
            history.append({"role": "assistant", "content": "trả lời {} ".format(index) * 400})
        executor = PrefetchExecutor(ToolResult(_blocks(3, 2500), _sources(3)))
        client = BudgetClient([ChatResponse("Đáp [1].", [], "stop")], n_ctx=8192)

        RagAgent(client, executor).ask("Câu hỏi hiện tại", history)

        sent = client.calls[0]["messages"]
        contents = [message["content"] for message in sent]
        self.assertEqual(sent[0]["content"], SYSTEM_PROMPT_VI)
        self.assertEqual(sent[-1], {"role": "user", "content": "Câu hỏi hiện tại"})
        self.assertTrue(any("nội dung " * 250 in text for text in contents))
        self.assertNotIn(history[0]["content"], contents)
        # The newest turn alone is over the room left: its question stays
        # and its answer keeps its beginning, rather than the answer alone.
        self.assertIn(history[-2]["content"], contents)
        self.assertTrue(any(text.endswith("…") and history[-1]["content"].startswith(text[:-1])
                            for text in contents))
        self.assertLessEqual(client.prompts[0], prompt_budget(8192))

    def test_trimming_drops_an_earlier_turn_whole(self):
        # An answer sent without its question reads as if nobody asked it.
        for answer_words in range(300, 420, 6):
            history = []
            for index in range(6):
                history.append({"role": "user", "content": "câu hỏi {} ".format(index) * 20})
                history.append({"role": "assistant", "content": "trả lời {} ".format(index) * answer_words})
            executor = PrefetchExecutor(ToolResult(_blocks(3, 2500), _sources(3)))
            client = BudgetClient([ChatResponse("Đáp [1].", [], "stop")], n_ctx=8192)

            RagAgent(client, executor).ask("Câu hỏi hiện tại", history)

            sent = [message for message in client.calls[0]["messages"][:-1]
                    if message["role"] in ("user", "assistant")]
            self.assertTrue(sent, answer_words)
            self.assertEqual([message["role"] for message in sent],
                             ["user", "assistant"] * (len(sent) // 2), answer_words)
            earlier = history[len(history) - len(sent):]
            for kept, original in zip(sent, earlier):
                self.assertTrue(original["content"].startswith(kept["content"].rstrip("…")),
                                answer_words)
            self.assertEqual(sent[-2], history[-2], answer_words)
            self.assertLessEqual(client.prompts[0], prompt_budget(8192))

    def test_server_overflow_is_trimmed_and_retried_once(self):
        executor = PrefetchExecutor(ToolResult(_blocks(6, 2000), _sources(6)))
        client = BudgetClient([ChatResponse("Đáp [1].", [], "stop")], overflows=1, server_n_ctx=4096)

        result = RagAgent(client, executor).ask("Hỏi")

        self.assertIsNone(result.error)
        self.assertEqual(len(client.calls), 1)
        self.assertLessEqual(client.prompts[0], prompt_budget(4096))
        self.assertGreaterEqual(client.max_tokens[0], 256)

    def test_second_overflow_gives_a_friendly_error_not_an_exception(self):
        executor = PrefetchExecutor(ToolResult(_blocks(2, 500), _sources(2)))
        client = BudgetClient([], overflows=2)

        result = RagAgent(client, executor).ask("Hỏi")

        self.assertEqual(result.error, CONTEXT_EXCEEDED_VI)
        self.assertEqual(result.answer, "")

    def test_answer_cut_at_the_length_limit_says_so(self):
        client = BudgetClient([ChatResponse("Câu trả lời dở dang [1]", [], "length")], n_ctx=8192)
        executor = PrefetchExecutor(ToolResult("nguồn", [_source()]))

        result = RagAgent(client, executor).ask("Hỏi")

        self.assertTrue(result.truncated)
        self.assertIn(TRUNCATED_NOTICE_VI, result.answer)
        self.assertTrue(result.answer.endswith(DISCLAIMER_VI))
        self.assertLess(result.answer.index(TRUNCATED_NOTICE_VI), result.answer.index(DISCLAIMER_VI))

    def test_complete_answer_is_not_marked_truncated(self):
        result = RagAgent(
            BudgetClient([ChatResponse("Đủ.", [], "stop")], n_ctx=8192), FakeExecutor([])
        ).ask("Hỏi")

        self.assertFalse(result.truncated)
        self.assertNotIn(TRUNCATED_NOTICE_VI, result.answer)


class BoundedHistoryTests(unittest.TestCase):
    def _turns(self, count, answer_chars=200):
        messages = []
        for index in range(count):
            messages.append({"role": "user", "content": "hỏi {}".format(index)})
            messages.append({"role": "assistant", "content": "đáp {} ".format(index) + "x" * answer_chars})
        return messages

    def test_footer_disclaimer_markers_and_truncation_notice_are_stripped(self):
        answer = (
            "Hộ chiếu có thời hạn 5 năm [1], gia hạn một lần [2][3].\n\n"
            "Nguồn: [1] [2]\n\n" + TRUNCATED_NOTICE_VI + "\n\n" + DISCLAIMER_VI
        )
        history = bounded_history(
            [{"role": "user", "content": "Hộ chiếu [1] là gì?"},
             {"role": "assistant", "content": answer}],
            16384,
        )

        self.assertEqual(history[0], {"role": "user", "content": "Hộ chiếu [1] là gì?"})
        self.assertEqual(
            history[1]["content"], "Hộ chiếu có thời hạn 5 năm, gia hạn một lần."
        )

    def test_an_answer_that_was_only_a_footer_is_left_out(self):
        history = bounded_history(
            [{"role": "user", "content": "hỏi"},
             {"role": "assistant", "content": "Nguồn: [1]\n\n" + DISCLAIMER_VI}],
            16384,
        )
        self.assertEqual(history, [{"role": "user", "content": "hỏi"}])

    def test_a_saved_answer_loses_its_source_line_but_keeps_inline_markers(self):
        saved = "Thời hạn 5 năm [1].\n\nNguồn: [1] [2]\n\n" + DISCLAIMER_VI

        self.assertEqual(
            "Thời hạn 5 năm [1].\n\n" + DISCLAIMER_VI, strip_source_footer(saved)
        )
        # A sentence that only starts with the word is not a source line.
        self.assertEqual("Nguồn: Bộ Công an [1]", strip_source_footer("Nguồn: Bộ Công an [1]"))

    def test_token_cap_is_800_on_the_cpu_and_1500_on_the_gpu(self):
        turns = self._turns(6, answer_chars=1500)

        def tokens(messages):
            return sum(estimate_prompt_tokens([message]) for message in messages)

        cpu, gpu = (bounded_history(turns, n_ctx) for n_ctx in (8192, 16384))
        self.assertLessEqual(tokens(cpu), 800)
        self.assertLessEqual(tokens(gpu), 1500)
        self.assertLess(len(cpu), len(gpu))
        self.assertEqual(gpu[-1], turns[-1])
        self.assertEqual(bounded_history(turns, 32768), gpu)

    def test_keeps_at_most_twelve_recent_messages_in_order(self):
        turns = self._turns(15, answer_chars=10)

        history = bounded_history(turns, 16384)

        self.assertEqual(len(history), 12)
        self.assertEqual(history, turns[-12:])

    def test_without_the_message_cap_the_token_budget_alone_decides(self):
        # Measured equal on bench/rag/multiturn (the conversation note names
        # the turns the cap drops), so the cap stays; 0 switches it off.
        import rag_agent

        turns = self._turns(15, answer_chars=10)
        saved = rag_agent.HISTORY_MESSAGES
        rag_agent.HISTORY_MESSAGES = 0
        try:
            self.assertEqual(bounded_history(turns, 16384), turns)
        finally:
            rag_agent.HISTORY_MESSAGES = saved

    def test_a_single_turn_over_the_cap_is_shortened_not_dropped(self):
        turns = [{"role": "user", "content": "hỏi"},
                 {"role": "assistant", "content": "đầu câu trả lời " + "chữ " * 2000}]

        history = bounded_history(turns, 8192)

        self.assertEqual(history[0], {"role": "user", "content": "hỏi"})
        self.assertEqual(len(history), 2)
        self.assertTrue(history[1]["content"].startswith("đầu câu trả lời"))
        self.assertLessEqual(estimate_prompt_tokens(history), 800)

    def test_an_answer_is_never_kept_without_its_question(self):
        # A 700-token answer fits the 800-token CPU cap but its 150-token
        # question does not: the answer is shortened, the question kept.
        question = {"role": "user", "content": "câu hỏi khá dài " * 24}
        answer = {"role": "assistant", "content": "câu trả lời " * 160}
        history = bounded_history(
            [{"role": "user", "content": "hỏi trước"},
             {"role": "assistant", "content": "đáp trước"}, question, answer],
            8192,
        )

        self.assertEqual([message["role"] for message in history], ["user", "assistant"])
        self.assertEqual(history[0], question)
        self.assertTrue(history[1]["content"].startswith("câu trả lời câu trả lời"))
        self.assertLessEqual(estimate_prompt_tokens(history), 800)

    def test_an_earlier_turn_that_does_not_fit_is_dropped_whole(self):
        turns = [{"role": "user", "content": "câu hỏi cũ " * 60},
                 {"role": "assistant", "content": "đáp cũ"},
                 {"role": "user", "content": "hỏi mới"},
                 {"role": "assistant", "content": " ".join(["câu trả lời mới"] * 120)}]

        history = bounded_history(turns, 8192)

        self.assertEqual(history, turns[2:])

    def test_a_shortened_capitalised_turn_still_fits_the_cap(self):
        # OCR headers are capitals and figures: a token each, so cutting by
        # lowercase characters per token alone would leave it over the cap.
        turns = [{"role": "user", "content": "hỏi"},
                 {"role": "assistant", "content": "QUYẾT ĐỊNH SỐ 12/2019/QĐ-UBND " * 300}]

        history = bounded_history(turns, 8192)

        self.assertEqual(len(history), 2)
        self.assertTrue(history[1]["content"].startswith("QUYẾT ĐỊNH"))
        self.assertLessEqual(estimate_prompt_tokens(history), 800)

    def test_unknown_context_uses_the_middle_cap(self):
        turns = self._turns(6, answer_chars=1500)
        self.assertEqual(bounded_history(turns, None), bounded_history(turns, 16384))


class ConversationLogicTests(unittest.TestCase):
    """What the assistant should do when the documents are not the point."""

    def test_greeting_skips_retrieval_tools_and_citations(self):
        client = FakeClient([ChatResponse("Xin chào! Tôi có thể giúp gì cho bạn?", [], "stop")])
        executor = PrefetchExecutor(ToolResult("Một đoạn văn bản", [_source()]))

        result = RagAgent(client, executor).ask("xin chào")

        self.assertEqual(executor.prefetch_questions, [])
        self.assertIsNone(client.calls[0]["tools"])
        self.assertEqual(result.citations, [])
        self.assertNotIn("Nguồn:", result.answer)
        self.assertNotIn(DISCLAIMER_VI, result.answer)

    def test_thanks_and_identity_questions_are_conversation(self):
        for text in ("cảm ơn bạn nhé", "Bạn là ai và có thể giúp gì cho tôi?", "ok"):
            executor = PrefetchExecutor(ToolResult("Một đoạn văn bản", [_source()]))
            RagAgent(FakeClient([ChatResponse("Vâng.", [], "stop")]), executor).ask(text)
            self.assertEqual(executor.prefetch_questions, [], text)

    def test_greeting_followed_by_a_real_question_still_searches(self):
        executor = PrefetchExecutor(ToolResult("Quyết định 147/QĐ-HĐND", [_source()]))
        RagAgent(FakeClient([ChatResponse("Theo [1].", [], "stop")]), executor).ask(
            "Chào bạn, Quyết định 147/QĐ-HĐND về việc gì?"
        )

        self.assertEqual(len(executor.prefetch_questions), 1)

    def test_citations_list_only_sources_that_support_the_answer(self):
        client = FakeClient(
            [ChatResponse("Ông Chương là Trưởng phòng Công tác Quốc hội.", [], "stop")]
        )
        executor = PrefetchExecutor(
            ToolResult(
                "Đối tượng: đồng chí Đỗ Xuân Chương, Trưởng phòng Công tác Quốc hội\n\n"
                "Kế hoạch tiếp xúc cử tri sau kỳ họp thứ Ba",
                [_source(5, 2, 118, "49-KH/TKT"), _source(8, 1, 140, "195/KH-HĐND")],
            )
        )

        result = RagAgent(client, executor).ask("Đỗ Xuân Chương có chức vụ là gì?")

        self.assertEqual([citation.n for citation in result.citations], [1])
        self.assertEqual([citation.doc_id for citation in result.citations], [5])
        self.assertNotIn("Nguồn:", result.answer)
        self.assertNotIn("[2]", result.answer)

    def test_general_knowledge_answer_rests_on_no_source(self):
        client = FakeClient(
            [ChatResponse("Theo kiến thức chung, UBND là viết tắt của Ủy ban nhân dân.", [], "stop")]
        )
        executor = PrefetchExecutor(
            ToolResult("Kế hoạch tiếp xúc cử tri sau kỳ họp thứ Ba", [_source()])
        )

        result = RagAgent(client, executor).ask("UBND là viết tắt của từ gì?")

        self.assertNotIn("Nguồn:", result.answer)
        self.assertEqual(result.citations, [])

    def test_weak_prefetch_is_not_offered_as_sources(self):
        client = FakeClient([ChatResponse("Theo kiến thức chung, đó là công tác văn thư.", [], "stop")])
        executor = PrefetchExecutor(
            ToolResult("Lịch tiếp xúc cử tri", [_source()], relevance=0.01)
        )

        result = RagAgent(client, executor).ask("Nghị định 30/2020/NĐ-CP quy định về vấn đề gì?")

        context = "\n".join(m["content"] for m in client.calls[0]["messages"] if m["role"] == "system")
        self.assertNotIn("Lịch tiếp xúc cử tri", context)
        self.assertIn("kiến thức chung", context)
        self.assertEqual(result.citations, [])

    def test_follow_up_question_searches_with_the_previous_question(self):
        history = [
            {"role": "user", "content": "Đỗ Xuân Chương có chức vụ là gì?"},
            {"role": "assistant", "content": "Ông là Trưởng phòng Công tác Quốc hội."},
        ]
        executor = PrefetchExecutor(ToolResult("Thời gian giám sát 10/8/2022", [_source()]))
        RagAgent(FakeClient([ChatResponse("Ngày 10/8/2022 [1].", [], "stop")]), executor).ask(
            "Ông ấy bị giám sát từ ngày nào?", history=history
        )

        self.assertIn("Đỗ Xuân Chương", executor.prefetch_questions[0])
        self.assertIn("giám sát", executor.prefetch_questions[0])

    def test_unclear_follow_up_is_searched_again_with_the_previous_question(self):
        class _Executor(PrefetchExecutor):
            def prefetch(self, question):
                self.prefetch_questions.append(question)
                strong = "101/KH-BPC" in question
                return ToolResult("Hạn đăng ký trước ngày 30/4/2018", [_source()],
                                  relevance=0.7 if strong else 0.05)

        history = [
            {"role": "user", "content": "Kế hoạch 101/KH-BPC của Ban Pháp chế về việc gì?"},
            {"role": "assistant", "content": "Tổ chức Đoàn công tác đi miền Trung."},
        ]
        executor = _Executor(None)
        result = RagAgent(
            FakeClient([ChatResponse("Trước ngày 30/4/2018 [1].", [], "stop")]), executor
        ).ask("Hạn đăng ký tham gia là ngày nào?", history=history)

        self.assertEqual(len(executor.prefetch_questions), 2)
        self.assertIn("101/KH-BPC", executor.prefetch_questions[1])
        self.assertEqual([citation.n for citation in result.citations], [1])

    def test_self_contained_question_is_searched_alone(self):
        history = [
            {"role": "user", "content": "Đỗ Xuân Chương có chức vụ là gì?"},
            {"role": "assistant", "content": "Trưởng phòng Công tác Quốc hội."},
        ]
        executor = PrefetchExecutor(ToolResult("Quyết định 147/QĐ-HĐND", [_source()]))
        question = "Quyết định 147/QĐ-HĐND cử đoàn công tác đi những tỉnh nào?"
        RagAgent(FakeClient([ChatResponse("Lâm Đồng [1].", [], "stop")]), executor).ask(
            question, history=history
        )

        self.assertEqual(executor.prefetch_questions, [question])

    def test_small_talk_fits_the_context_like_any_question(self):
        history = []
        for index in range(6):
            history.append({"role": "user", "content": "câu hỏi {} ".format(index) * 20})
            history.append({"role": "assistant", "content": "trả lời {} ".format(index) * 400})
        client = BudgetClient([ChatResponse("Không có gì!", [], "stop")], n_ctx=8192)

        result = RagAgent(client, PrefetchExecutor(None)).ask("cảm ơn bạn", history)

        self.assertEqual(result.steps, ["conversation"])
        self.assertLessEqual(client.prompts[0], prompt_budget(8192))
        self.assertEqual(client.calls[0]["messages"][-1], {"role": "user", "content": "cảm ơn bạn"})
        self.assertEqual(result.answer, "Không có gì!")

    def test_small_talk_overflow_gives_the_friendly_error(self):
        client = BudgetClient([], overflows=2)

        result = RagAgent(client, PrefetchExecutor(None)).ask("xin chào")

        self.assertEqual(result.error, CONTEXT_EXCEEDED_VI)

    def test_small_talk_cut_at_the_length_limit_says_so(self):
        client = BudgetClient([ChatResponse("Xin chào, tôi", [], "length")], n_ctx=8192)

        result = RagAgent(client, PrefetchExecutor(None)).ask("xin chào")

        self.assertTrue(result.truncated)
        self.assertTrue(result.answer.endswith(TRUNCATED_NOTICE_VI))
        self.assertNotIn(DISCLAIMER_VI, result.answer)


class _MemoryExecutor:
    """An executor that resolves documents like ToolExecutor, from a small
    catalog, and records every prefetch."""

    def __init__(self, relevance=None, scope=None):
        from doc_resolver import DocumentCatalog

        self.catalog = DocumentCatalog([
            (3, "166-KH/ĐĐHĐND", "Kế hoạch", "08/04/2022", "Phòng, chống tham nhũng"),
            (8, "195/KH-HĐND", "Kế hoạch", "02/06/2017", "Tiếp xúc cử tri"),
            (7, "336/KH-KTNS", "Kế hoạch", "02/08/2017", "Quỹ phát triển đất"),
        ])
        self.relevance = relevance
        self.scope_doc_ids = scope
        self.prefetches = []
        self.calls = []

    def resolve(self, question, state=None):
        from doc_resolver import resolve

        open_doc = None
        if state is None and self.scope_doc_ids and len(self.scope_doc_ids) == 1:
            open_doc = self.scope_doc_ids[0]
        return resolve(question, self.catalog, state, open_doc=open_doc)

    def prefetch(self, question, documents=None, others=True):
        self.prefetches.append((question, list(documents) if documents else None))
        if documents:
            sources = [_source(doc_id, 1, 100 + doc_id, self.catalog.label(doc_id)) for doc_id in documents]
            text = "\n\n".join("Nội dung văn bản {}".format(self.catalog.label(d)) for d in documents)
            return ToolResult(text, sources, documents=list(documents) if len(documents) > 1 else None)
        return ToolResult("Một đoạn không liên quan", [_source(7, 1, 70, "336/KH-KTNS")],
                          relevance=self.relevance)

    def execute(self, name, arguments):
        self.calls.append((name, arguments))
        return ToolResult("", [])


def _system_text(client, call=0):
    return "\n".join(m["content"] for m in client.calls[call]["messages"] if m["role"] == "system")


class ConversationMemoryTests(unittest.TestCase):
    """Documents named or pointed at, the conversation state, questions
    about the conversation (bench/rag/multiturn)."""

    def _state(self, *turns, open_doc=None, scope="all"):
        from doc_resolver import ConversationState

        return ConversationState(list(turns), open_doc=open_doc, scope=scope)

    def _turn(self, question, docs, **kwargs):
        from doc_resolver import TurnRecord

        return TurnRecord(question=question, docs=list(docs), **kwargs)

    def test_documents_named_by_bare_numbers_are_both_shown_whatever_their_score(self):
        executor = _MemoryExecutor(relevance=0.0)
        client = FakeClient([ChatResponse("Văn bản 195 về cử tri [1], 166 về tham nhũng [2].", [], "stop")])

        result = RagAgent(client, executor).ask("văn bản 195 và văn bản 166 khác nhau thế nào")

        self.assertEqual([("văn bản 195 và văn bản 166 khác nhau thế nào", [8, 3])], executor.prefetches)
        self.assertEqual([8, 3], result.refs)
        self.assertEqual({8: "195/KH-HĐND", 3: "166-KH/ĐĐHĐND"}, result.labels)
        context = _system_text(client)
        self.assertIn("Nội dung văn bản 195/KH-HĐND", context)
        self.assertIn("Nguồn theo văn bản: 195/KH-HĐND: [1]; 166-KH/ĐĐHĐND: [2]", context)
        self.assertEqual([8, 3], [citation.doc_id for citation in result.citations])

    def test_a_named_document_widens_the_open_document_scope_and_says_so(self):
        executor = _MemoryExecutor(scope=[21])
        agent = RagAgent(FakeClient([ChatResponse("Trần Quốc Toàn ký [1].", [], "stop")]), executor)
        agent.conversation = self._state(open_doc=21, scope="open")

        result = agent.ask("Kế hoạch 336/KH-KTNS do ai ký?")

        self.assertEqual([21, 7], executor.scope_doc_ids)
        self.assertTrue(result.answer.startswith(
            "Ngoài phạm vi tài liệu đang mở: đã tra cả 336/KH-KTNS vì câu hỏi nêu văn bản này."))

    def test_no_notice_when_the_question_itself_set_the_scope(self):
        executor = _MemoryExecutor(scope=[8])
        agent = RagAgent(FakeClient([ChatResponse("So sánh [1] [2].", [], "stop")]), executor)
        agent.conversation = self._state(open_doc=8, scope="all")

        result = agent.ask("so sánh văn bản đang mở với kế hoạch 166")

        self.assertEqual([8, 3], executor.scope_doc_ids)
        self.assertFalse(result.answer.startswith("Ngoài phạm vi"))

    def test_a_follow_up_with_weak_sources_reads_the_last_turns_document(self):
        executor = _MemoryExecutor(relevance=0.01)
        agent = RagAgent(FakeClient([ChatResponse("Hội trường 408 [1].", [], "stop")]), executor)
        agent.conversation = self._state(self._turn("Kế hoạch 195/KH-HĐND căn cứ vào đâu?", [8]))

        result = agent.ask("Họp ở đâu?", history=[
            {"role": "user", "content": "Kế hoạch 195/KH-HĐND căn cứ vào đâu?"},
            {"role": "assistant", "content": "Căn cứ Luật."}])

        # Searched in that document together with the previous question,
        # which gives the few words their subject.
        self.assertEqual([("Họp ở đâu?", None),
                          ("Kế hoạch 195/KH-HĐND căn cứ vào đâu? Họp ở đâu?", [8])], executor.prefetches)
        self.assertEqual([8], result.refs)
        self.assertEqual({8: "follow-up"}, result.how)
        self.assertEqual([8], result.shown)
        self.assertEqual([8], [citation.doc_id for citation in result.citations])

    def test_a_pronoun_reads_the_last_document_when_the_search_finds_it(self):
        class _Found(_MemoryExecutor):
            def prefetch(self, question, documents=None, others=True):
                if documents:
                    return super().prefetch(question, documents)
                self.prefetches.append((question, None))
                return ToolResult("A\n\nB", [_source(22, 1, 1, "117/QĐ-HĐND"), _source(21, 1, 2, "101/KH-BPC")],
                                  relevance=0.9)

        executor = _Found()
        agent = RagAgent(FakeClient([ChatResponse("Lê Văn Hưng [1].", [], "stop")]), executor)
        agent.conversation = self._state(self._turn("Hạn đăng ký tham gia đoàn là ngày nào?", [21]))

        result = agent.ask("Trưởng đoàn của đoàn công tác đó là ai?")

        self.assertEqual([21], executor.prefetches[-1][1])
        self.assertEqual({21: "follow-up"}, result.how)

    def test_a_pronoun_about_a_new_subject_keeps_the_search(self):
        executor = _MemoryExecutor(relevance=0.9)  # finds only 336/KH-KTNS (doc 7)
        agent = RagAgent(FakeClient([ChatResponse("Theo [1].", [], "stop")]), executor)
        agent.conversation = self._state(self._turn("Tờ trình về Tổ thư ký?", [28]))

        result = agent.ask("Kế hoạch giám sát đảng viên do bà ấy ký giám sát ai?")

        self.assertEqual([("Kế hoạch giám sát đảng viên do bà ấy ký giám sát ai?", None)], executor.prefetches)
        self.assertEqual([], result.refs)

    def test_an_ocr_slip_in_the_number_is_said_next_to_the_question(self):
        from doc_resolver import DocumentCatalog

        executor = _MemoryExecutor()
        executor.catalog = DocumentCatalog([(23, "4243-QĐ/TU", "Quyết định", "25/07/2018", "Cử cán bộ đi học")])
        client = FakeClient([ChatResponse("Trần Quốc Văn [1].", [], "stop")])

        RagAgent(client, executor).ask("Quyết định 1213-QĐ/TU cử ai đi học?")

        # The number is written as stored, and the note says what was asked
        # (told only in the note, the model answered "không tìm thấy" 3/3).
        question = client.calls[0]["messages"][-1]["content"]
        self.assertTrue(question.startswith("Quyết định 4243-QĐ/TU cử ai đi học?\n\n"))
        self.assertIn("4243-QĐ/TU (câu hỏi ghi 1213-QĐ/TU;", question)

    def test_a_question_naming_its_documents_is_sent_as_asked(self):
        client = FakeClient([ChatResponse("Theo [1].", [], "stop")])
        RagAgent(client, _MemoryExecutor()).ask("Kế hoạch 336/KH-KTNS về việc gì?")
        self.assertEqual("Kế hoạch 336/KH-KTNS về việc gì?", client.calls[0]["messages"][-1]["content"])

    def test_a_strong_search_is_kept_for_a_follow_up(self):
        executor = _MemoryExecutor(relevance=0.9)
        agent = RagAgent(FakeClient([ChatResponse("Theo [1].", [], "stop")]), executor)
        agent.conversation = self._state(self._turn("Kế hoạch 195?", [8]))

        agent.ask("Họp ở đâu?")

        self.assertEqual([("Họp ở đâu?", None)], executor.prefetches)

    def test_a_long_general_question_is_not_tied_to_the_last_document(self):
        executor = _MemoryExecutor(relevance=0.01)
        client = FakeClient([ChatResponse("Theo kiến thức chung, đó là cơ quan quyền lực.", [], "stop")])
        agent = RagAgent(client, executor)
        agent.conversation = self._state(self._turn("Kế hoạch 195?", [8]))

        result = agent.ask("Hội đồng nhân dân tỉnh là cơ quan gì trong bộ máy nhà nước?")

        self.assertEqual(1, len(executor.prefetches))
        self.assertEqual([], result.citations)
        self.assertIn("kiến thức chung", _system_text(client))

    def test_the_conversation_state_goes_after_the_history_and_before_the_sources(self):
        executor = _MemoryExecutor()
        client = FakeClient([ChatResponse("Ngày 08/04/2022 [1].", [], "stop")])
        agent = RagAgent(client, executor)
        agent.conversation = self._state(
            self._turn("tóm tắt về văn bản tôi đang mở", [3], open_doc=3, scope="open", how="scope"),
            self._turn("tóm tắt về văn bản đang mở", [8], open_doc=8, scope="open", how="scope"),
            open_doc=8)
        history = [{"role": "user", "content": "tóm tắt"}, {"role": "assistant", "content": "Tóm tắt."}]

        result = agent.ask("văn bản đầu tiên tôi hỏi ban hành ngày nào?", history=history)

        messages = client.calls[0]["messages"]
        roles = [m["role"] for m in messages]
        self.assertEqual(["system", "user", "assistant", "system", "system", "user"], roles)
        self.assertIn("TRẠNG THÁI HỘI THOẠI", messages[3]["content"])
        self.assertIn("Lượt 1: \"tóm tắt về văn bản tôi đang mở\" → 166-KH/ĐĐHĐND", messages[3]["content"])
        self.assertIn("NGUỒN TRONG KHO", messages[4]["content"])
        # Which document "văn bản đầu tiên" meant is said in the note; the
        # question goes as asked (next to it measured equal).
        self.assertIn("Câu hỏi hiện tại được hiểu là về: 166-KH/ĐĐHĐND (văn bản của lượt đầu)",
                      messages[3]["content"])
        self.assertEqual("văn bản đầu tiên tôi hỏi ban hành ngày nào?", messages[5]["content"])
        self.assertEqual([3], result.refs)

    def test_the_resolution_can_be_said_next_to_the_question(self):
        import rag_agent

        executor = _MemoryExecutor()
        client = FakeClient([ChatResponse("Ngày 08/04/2022 [1].", [], "stop")])
        agent = RagAgent(client, executor)
        agent.conversation = self._state(
            self._turn("tóm tắt văn bản đang mở", [3], open_doc=3, scope="open", how="scope"), open_doc=8)
        saved = rag_agent.UNDERSTOOD_LINE
        rag_agent.UNDERSTOOD_LINE = True
        try:
            agent.ask("văn bản đầu tiên tôi hỏi ban hành ngày nào?")
        finally:
            rag_agent.UNDERSTOOD_LINE = saved
        self.assertEqual("văn bản đầu tiên tôi hỏi ban hành ngày nào?\n\n(Ứng dụng hiểu câu hỏi này là về: "
                         "166-KH/ĐĐHĐND (văn bản của lượt đầu).)", client.calls[0]["messages"][-1]["content"])

    def test_the_state_is_smaller_in_the_cpu_context(self):
        from llm_client import estimate_text_tokens
        from rag_agent import state_token_budget

        self.assertEqual(450, state_token_budget(16384))
        self.assertEqual(450, state_token_budget(None))
        self.assertEqual(300, state_token_budget(8192))
        executor = _MemoryExecutor()
        client = BudgetClient([ChatResponse("Theo [1].", [], "stop")], n_ctx=8192)
        agent = RagAgent(client, executor)
        agent.conversation = self._state(*[
            self._turn("Câu hỏi khá dài số {} về kế hoạch tiếp xúc cử tri".format(index), [8, 3, 7])
            for index in range(10)])

        agent.ask("Kế hoạch 336/KH-KTNS về việc gì?")

        state = next(m["content"] for m in client.calls[0]["messages"]
                     if m["content"].startswith("TRẠNG THÁI HỘI THOẠI"))
        self.assertLessEqual(estimate_text_tokens(state), 300)

    def test_a_single_question_sends_no_state(self):
        client = FakeClient([ChatResponse("Theo [1].", [], "stop")])
        RagAgent(client, _MemoryExecutor()).ask("Kế hoạch 336/KH-KTNS về việc gì?")
        self.assertNotIn("TRẠNG THÁI HỘI THOẠI", _system_text(client))

    def test_a_question_about_the_conversation_is_answered_without_retrieval(self):
        executor = _MemoryExecutor()
        client = FakeClient([ChatResponse("Ở đầu cuộc trò chuyện bạn hỏi về 166-KH/ĐĐHĐND [1].", [], "stop")])
        agent = RagAgent(client, executor)
        turn = self._turn("tóm tắt về văn bản tôi đang mở", [3], open_doc=3, scope="open", how="scope")
        turn.answer = "Văn bản đang mở là Kế hoạch 166-KH/ĐĐHĐND."
        agent.conversation = self._state(turn, open_doc=8)

        result = agent.ask("ở đầu đoạn chat tôi đã hỏi bạn về văn bản nào")

        self.assertEqual([], executor.prefetches)
        self.assertIsNone(client.calls[0]["tools"])
        self.assertEqual([], result.citations)
        self.assertEqual("meta", result.intent)
        self.assertEqual(["conversation"], result.steps)
        self.assertNotIn("[1]", result.answer)
        self.assertNotIn(DISCLAIMER_VI, result.answer)
        state = _system_text(client)
        self.assertIn("đã trả lời: \"Văn bản đang mở là Kế hoạch 166-KH/ĐĐHĐND.\"", state)

    def test_small_talk_in_several_words_is_conversation(self):
        for text in ("ok cảm ơn", "tốt quá, cảm ơn", "cảm ơn, tạm biệt", "hay quá"):
            executor = _MemoryExecutor()
            result = RagAgent(FakeClient([ChatResponse("Vâng.", [], "stop")]), executor).ask(text)
            self.assertEqual([], executor.prefetches, text)
            self.assertEqual("conversation", result.intent, text)

    def test_switches_restore_the_old_behaviour(self):
        import rag_agent

        executor = _MemoryExecutor(relevance=0.0)
        saved = rag_agent.RESOLVE_DOCUMENTS
        rag_agent.RESOLVE_DOCUMENTS = False
        try:
            RagAgent(FakeClient([ChatResponse("Không tìm thấy thông tin.", [], "stop")]), executor).ask(
                "văn bản 195 và văn bản 166 khác nhau thế nào")
        finally:
            rag_agent.RESOLVE_DOCUMENTS = saved
        self.assertEqual([("văn bản 195 và văn bản 166 khác nhau thế nào", None)], executor.prefetches)


class CreditPerDocumentTests(unittest.TestCase):
    def test_a_comparison_credits_both_documents(self):
        text = "Hộ chiếu ngoại giao có thời hạn 5 năm kể từ ngày cấp"
        sources = [_source(3, 1, 30 + n, "166-KH/ĐĐHĐND") for n in range(4)] + [
            _source(8, 1, 80 + n, "195/KH-HĐND") for n in range(2)]
        client = FakeClient([
            ChatResponse("", [_call()], "tool_calls"),
            ChatResponse("Hộ chiếu ngoại giao có thời hạn 5 năm.", [], "stop"),
        ])
        result = RagAgent(client, FakeExecutor([ToolResult("\n\n".join([text] * 6), sources)])).ask("Hỏi")

        self.assertEqual([1, 2, 5, 6], [citation.n for citation in result.citations])
        self.assertEqual({3, 8}, {citation.doc_id for citation in result.citations})


class _TableExecutor(_MemoryExecutor):
    """A _MemoryExecutor whose store answers metadata questions with a table."""

    def __init__(self, table=None, **kwargs):
        super().__init__(**kwargs)
        self.table = table
        self.aggregates = []

    def aggregate(self, question, conversation=None, within=None):
        self.aggregates.append((question, within))
        return self.table


def _plans_table():
    from metadata_query import Aggregate

    blocks = ["THỐNG KÊ TỪ THÔNG TIN VĂN BẢN. KẾT QUẢ: 2 văn bản — theo loại: Kế hoạch 2.\n"
              "195/KH-HĐND · Kế hoạch · 02/06/2017", "336/KH-KTNS · Kế hoạch · 02/08/2017"]
    aggregate = Aggregate("documents", blocks, [(8, "195/KH-HĐND"), (7, "336/KH-KTNS")], [8, 7],
                          totals=[2], items=[("195/KH-HĐND", ["195/KH-HĐND"]), ("336/KH-KTNS", ["336/KH-KTNS"])])
    return ToolResult("\n\n".join(blocks), [_source(8, None, None, "195/KH-HĐND"),
                                            _source(7, None, None, "336/KH-KTNS")], aggregate=aggregate)


class TableAndAnswerCheckTests(unittest.TestCase):
    """Metadata questions answered from a table (metadata_query) and answers
    checked against what the model was shown (answer_check)."""

    def test_a_metadata_question_is_answered_from_the_table_without_tools(self):
        import rag_agent

        executor = _TableExecutor(_plans_table())
        client = FakeClient([ChatResponse("Có 2 kế hoạch: 195/KH-HĐND [1] và 336/KH-KTNS [2].", [], "stop")])
        result = RagAgent(client, executor).ask("Liệt kê các kế hoạch")

        self.assertEqual([("Liệt kê các kế hoạch", None)], executor.aggregates)
        self.assertEqual([], executor.prefetches)
        self.assertIsNone(client.calls[0]["tools"])
        self.assertIn(rag_agent.AGGREGATE_NOTE_VI, _system_text(client))
        self.assertEqual("documents", result.table)
        self.assertEqual([8, 7], result.shown)
        self.assertEqual([], result.check["violations"])
        self.assertEqual(1, len(client.calls))

    def test_a_named_document_is_not_counted(self):
        executor = _TableExecutor(_plans_table())
        RagAgent(FakeClient([ChatResponse("Đặng Minh Ngọc [1].", [], "stop")]), executor).ask(
            "Kế hoạch 195/KH-HĐND do ai ký?")
        self.assertEqual([], executor.aggregates)

    def test_a_list_that_leaves_a_row_out_is_asked_again(self):
        executor = _TableExecutor(_plans_table())
        steps = []
        client = FakeClient([ChatResponse("Có 2 kế hoạch, ví dụ 195/KH-HĐND [1].", [], "stop"),
                             ChatResponse("Có 2 kế hoạch: 195/KH-HĐND [1] và 336/KH-KTNS [2].", [], "stop")])
        result = RagAgent(client, executor, step_cb=steps.append).ask("Liệt kê các kế hoạch")

        self.assertEqual(2, len(client.calls))
        correction = client.calls[1]["messages"][-1]
        self.assertEqual("user", correction["role"])
        self.assertTrue(correction["content"].startswith("KIỂM TRA TỰ ĐỘNG"))
        self.assertIn("336/KH-KTNS", correction["content"])
        self.assertIn("KẾT QUẢ: 2 văn bản — theo loại: Kế hoạch 2.", correction["content"])
        self.assertEqual("assistant", client.calls[1]["messages"][-2]["role"])
        self.assertIsNone(client.calls[1]["tools"])
        self.assertIn("336/KH-KTNS", result.answer)
        self.assertEqual(["list"], result.check["violations"])
        self.assertTrue(result.check["regenerated"])
        self.assertEqual([], result.check["after"])
        self.assertIn("verify_answer", result.steps)
        self.assertIn("verify_answer", steps)

    def test_a_figure_in_no_source_is_corrected_or_taken_out(self):
        executor = _MemoryExecutor()
        wrong = "Kế hoạch 195/KH-HĐND về tiếp xúc cử tri [1]. Hai văn bản cách nhau 114 ngày."
        client = FakeClient([ChatResponse(wrong, [], "stop"), ChatResponse(wrong, [], "stop")])
        result = RagAgent(client, executor).ask("Kế hoạch 195/KH-HĐND nói về gì?")

        self.assertIn("\"114\"", client.calls[1]["messages"][-1]["content"])
        self.assertNotIn("114", result.answer)
        self.assertTrue(result.answer.startswith("Kế hoạch 195/KH-HĐND về tiếp xúc cử tri [1]."))
        self.assertEqual(["figure"], result.check["after"])
        self.assertTrue(result.check["hedged"])
        self.assertEqual([8], [citation.doc_id for citation in result.citations])

    def test_a_document_called_by_another_type_keeps_its_own(self):
        executor = _MemoryExecutor()
        wrong = "Tờ trình 195/KH-HĐND về tiếp xúc cử tri [1]."
        client = FakeClient([ChatResponse(wrong, [], "stop"), ChatResponse(wrong, [], "stop")])
        result = RagAgent(client, executor).ask("Văn bản 195/KH-HĐND nói về gì?")
        self.assertTrue(result.answer.startswith("Kế hoạch 195/KH-HĐND về tiếp xúc cử tri [1]."))

    def test_not_found_with_the_named_document_shown_is_asked_again(self):
        executor = _MemoryExecutor()
        client = FakeClient([ChatResponse("Không tìm thấy thông tin.", [], "stop"),
                             ChatResponse("Kế hoạch 195/KH-HĐND về tiếp xúc cử tri [1].", [], "stop")])
        result = RagAgent(client, executor).ask("Kế hoạch 195/KH-HĐND nói về gì?")
        self.assertIn("195/KH-HĐND (nguồn [1])", client.calls[1]["messages"][-1]["content"])
        self.assertTrue(result.answer.startswith("Kế hoạch 195/KH-HĐND"))

    def test_switched_off_or_without_a_catalog_nothing_is_checked(self):
        import rag_agent

        wrong = "Kế hoạch 195/KH-HĐND [1]. Cách nhau 114 ngày."
        rag_agent.ANSWER_CHECK = False
        try:
            client = FakeClient([ChatResponse(wrong, [], "stop")])
            result = RagAgent(client, _MemoryExecutor()).ask("Kế hoạch 195/KH-HĐND nói về gì?")
        finally:
            rag_agent.ANSWER_CHECK = True
        self.assertEqual(1, len(client.calls))
        self.assertIsNone(result.check)
        self.assertIn("114", result.answer)
        client = FakeClient([ChatResponse(wrong, [], "stop")])
        result = RagAgent(client, PrefetchExecutor(ToolResult("Tiếp xúc cử tri", [_source(8)]))).ask("Hỏi")
        self.assertEqual(1, len(client.calls))
        self.assertIsNone(result.check)

    def test_tables_switched_off(self):
        import rag_agent

        executor = _TableExecutor(_plans_table())
        rag_agent.AGGREGATES = False
        try:
            RagAgent(FakeClient([ChatResponse("Kế hoạch [1].", [], "stop")]), executor).ask("Liệt kê các kế hoạch")
        finally:
            rag_agent.AGGREGATES = True
        self.assertEqual([], executor.aggregates)
        self.assertEqual(1, len(executor.prefetches))


class TurnRecordTests(unittest.TestCase):
    def test_saved_context_and_old_rows_without_it(self):
        from rag_agent import turn_records

        class _Message(tuple):
            def __new__(cls, role, content, citations, meta=None):
                message = super().__new__(cls, (role, content, citations))
                message.meta = meta or {}
                return message

        messages = [
            _Message("user", "tóm tắt văn bản đang mở", [], {"scope": "open", "open_doc": 3}),
            _Message("assistant", "Kế hoạch về tham nhũng [1].", [Citation(1, 3, 1, "166-KH/ĐĐHĐND", 30)],
                     {"intent": "documents", "status": "answered", "refs": [3], "how": {"3": "scope"},
                      "labels": {"3": "166-KH/ĐĐHĐND"}}),
            ("user", "so sánh 195 và 166", []),          # saved before the context existed
            ("assistant", "Không tìm thấy thông tin về văn bản 166.", []),
            ("user", "Văn bản 195?", []),
            ("assistant", "Tiếp xúc cử tri [1].", [Citation(1, 8, 2, "195/KH-HĐND", 80)]),
            ("user", "cảm ơn", []),
            ("assistant", "Không có gì.", []),
            ("user", "câu bị dừng", []),
        ]
        records = turn_records(messages)

        self.assertEqual(4, len(records))
        self.assertEqual(([3], 3, "open", "answered", "scope"),
                         (records[0].docs, records[0].open_doc, records[0].scope, records[0].status,
                          records[0].how))
        self.assertEqual(([], None, None, "not_found"),
                         (records[1].docs, records[1].open_doc, records[1].scope, records[1].status))
        self.assertEqual(([8], "cited", {8: "195/KH-HĐND"}),
                         (records[2].docs, records[2].how, records[2].labels))
        self.assertEqual("conversation", records[3].status)

    def test_history_can_mark_not_found_answers_and_name_pointed_documents(self):
        from rag_agent import NOT_ANSWERED_VI, conversation_history

        class _Message(tuple):
            def __new__(cls, role, content, citations, meta=None):
                message = super().__new__(cls, (role, content, citations))
                message.meta = meta or {}
                return message

        messages = [
            _Message("user", "tóm tắt văn bản tôi đang mở", []),
            _Message("assistant", "Văn bản đang mở là Kế hoạch 166.", [],
                     {"refs": [3], "how": {"3": "scope"}, "labels": {"3": "166-KH/ĐĐHĐND"}}),
            _Message("user", "Kế hoạch 195/KH-HĐND về gì?", []),
            _Message("assistant", "Tiếp xúc cử tri.", [],
                     {"refs": [8], "how": {"8": "named"}, "labels": {"8": "195/KH-HĐND"}}),
            _Message("user", "so sánh 195 và 166", []),
            _Message("assistant", "Không tìm thấy thông tin về văn bản số 166.", []),
        ]
        history = conversation_history(messages, 16384)

        # As before by default: both measured equal on bench/rag/multiturn.
        self.assertEqual("tóm tắt văn bản tôi đang mở", history[0]["content"])
        self.assertEqual("Không tìm thấy thông tin về văn bản số 166.", history[5]["content"])

        import rag_agent
        saved = rag_agent.ANNOTATE_HISTORY, rag_agent.MARK_NOT_FOUND
        rag_agent.ANNOTATE_HISTORY, rag_agent.MARK_NOT_FOUND = True, True
        try:
            history = conversation_history(messages, 16384)
        finally:
            rag_agent.ANNOTATE_HISTORY, rag_agent.MARK_NOT_FOUND = saved
        self.assertEqual("tóm tắt văn bản tôi đang mở [= 166-KH/ĐĐHĐND]", history[0]["content"])
        self.assertEqual("Kế hoạch 195/KH-HĐND về gì?", history[2]["content"])
        self.assertEqual(NOT_ANSWERED_VI, history[5]["content"])


if __name__ == "__main__":
    unittest.main()
