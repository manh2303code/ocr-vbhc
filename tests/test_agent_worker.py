import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication

from background_workers import AgentWorker
from rag_agent import AgentResult


class _FakeAgent:
    def __init__(self, result=None, exc=None):
        self.result = result or AgentResult("Trả lời [1]", [], ["search_documents"])
        self.exc = exc
        self.step_cb = None
        self.token_cb = None
        self.cancel_check = lambda: False
        self.calls = []

    def ask(self, question, history=None):
        self.calls.append((question, history))
        if self.exc:
            raise self.exc
        self.step_cb("search_documents")
        self.token_cb("Trả")
        if self.cancel_check():
            return AgentResult("", [], [], "cancelled")
        return self.result


class AgentWorkerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # QApplication also satisfies QCoreApplication-based signal tests and
        # lets later widget tests reuse the same singleton safely.
        cls.app = QApplication.instance() or QApplication([])

    def test_run_emits_finished_result_and_forwards_input(self):
        agent = _FakeAgent()
        worker = AgentWorker(agent, "Hộ chiếu được cấp thế nào?", [{"role": "user"}])
        results = []
        worker.finished_result.connect(results.append)

        worker.run()

        self.assertEqual([agent.result], results)
        self.assertEqual(
            [("Hộ chiếu được cấp thế nào?", [{"role": "user"}])], agent.calls
        )

    def test_cancel_before_run_is_visible_to_agent(self):
        agent = _FakeAgent()
        worker = AgentWorker(agent, "Câu hỏi")
        results = []
        worker.finished_result.connect(results.append)

        worker.cancel()
        worker.run()

        self.assertEqual("cancelled", results[0].error)

    def test_existing_agent_cancellation_is_still_respected(self):
        agent = _FakeAgent()
        agent.cancel_check = lambda: True
        worker = AgentWorker(agent, "Câu hỏi")
        results = []
        worker.finished_result.connect(results.append)

        worker.run()

        self.assertEqual("cancelled", results[0].error)

    def test_callbacks_emit_step_and_token_signals(self):
        worker = AgentWorker(_FakeAgent(), "Câu hỏi")
        steps, tokens = [], []
        worker.step.connect(steps.append)
        worker.token.connect(tokens.append)

        worker.run()

        self.assertEqual(["search_documents"], steps)
        self.assertEqual(["Trả"], tokens)

    def test_existing_callbacks_and_cancel_check_are_preserved(self):
        agent = _FakeAgent()
        old_steps, old_tokens = [], []
        agent.step_cb = old_steps.append
        agent.token_cb = old_tokens.append
        agent.cancel_check = lambda: False
        original = (agent.step_cb, agent.token_cb, agent.cancel_check)

        worker = AgentWorker(agent, "Câu hỏi")
        worker.run()

        self.assertEqual(["search_documents"], old_steps)
        self.assertEqual(["Trả"], old_tokens)
        self.assertEqual(original, (agent.step_cb, agent.token_cb, agent.cancel_check))

    def test_cancel_during_stream_abandons_generation_at_the_next_token(self):
        """Stop used to wait for the whole answer to finish streaming: the
        agent only looked at cancel_check between model calls."""

        class StreamingAgent(_FakeAgent):
            def __init__(self):
                super().__init__()
                self.sent = 0

            def ask(self, question, history=None):
                for _ in range(200):
                    self.sent += 1
                    self.token_cb("tok ")
                return AgentResult("đủ", [], [])

        agent = StreamingAgent()
        worker = AgentWorker(agent, "Câu hỏi")
        results, errors, shown = [], [], []
        worker.finished_result.connect(results.append)
        worker.error.connect(errors.append)
        worker.token.connect(shown.append)
        worker.token.connect(
            lambda _token: worker.cancel() if agent.sent == 3 else None
        )

        worker.run()

        # The token after the click is neither shown nor waited for.
        self.assertEqual(3, len(shown))
        self.assertEqual(4, agent.sent)
        self.assertEqual([], errors)
        self.assertEqual("cancelled", results[0].error)
        self.assertIsNone(agent.token_cb)

    def test_sources_are_bridged_to_a_signal_and_the_callback_restored(self):
        class SourceAgent(_FakeAgent):
            def __init__(self):
                super().__init__()
                self.sources_cb = None

            def ask(self, question, history=None):
                self.sources_cb(["nguồn 1"])
                return self.result

        agent = SourceAgent()
        earlier = []
        agent.sources_cb = earlier.append
        worker = AgentWorker(agent, "Câu hỏi")
        seen = []
        worker.sources.connect(seen.append)

        worker.run()

        self.assertEqual([["nguồn 1"]], seen)
        self.assertEqual([["nguồn 1"]], earlier)
        self.assertEqual(earlier.append, agent.sources_cb)

    def test_exception_emits_error_without_finished_result(self):
        worker = AgentWorker(_FakeAgent(exc=RuntimeError("server unavailable")), "bí mật")
        errors, results = [], []
        worker.error.connect(errors.append)
        worker.finished_result.connect(results.append)

        worker.run()

        self.assertEqual(["server unavailable"], errors)
        self.assertEqual([], results)


if __name__ == "__main__":
    unittest.main()
