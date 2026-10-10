import unittest

from shutdown_lifecycle import shutdown_runtime


class _Worker:
    def __init__(self, events, name, stops=True):
        self.events = events
        self.name = name
        self.running = True
        self.stops = stops

    def isRunning(self):
        return self.running

    def cancel(self):
        self.events.append(f"cancel:{self.name}")

    def wait(self, _timeout):
        self.events.append(f"wait:{self.name}")
        if self.stops:
            self.running = False
        return self.stops


class _Closable:
    def __init__(self, events, name, method):
        self.events = events
        self.name = name
        setattr(self, method, self._record)

    def _record(self):
        self.events.append(self.name)


class ShutdownLifecycleTests(unittest.TestCase):
    def test_workers_stop_before_clients_models_server_and_database(self):
        events = []
        failures = shutdown_runtime(
            workers=[_Worker(events, "ocr"), _Worker(events, "embed")],
            clients=[_Closable(events, "client", "close")],
            models=[_Closable(events, "model", "unload")],
            servers=[_Closable(events, "server", "stop")],
            database=_Closable(events, "database", "close"),
            timeout_ms=100,
        )

        self.assertEqual([], failures)
        self.assertLess(events.index("wait:embed"), events.index("client"))
        self.assertLess(events.index("client"), events.index("model"))
        self.assertLess(events.index("model"), events.index("server"))
        self.assertLess(events.index("server"), events.index("database"))

    def test_dependencies_remain_open_when_a_worker_does_not_stop(self):
        events = []
        failures = shutdown_runtime(
            workers=[_Worker(events, "stuck", stops=False)],
            clients=[_Closable(events, "client", "close")],
            models=[],
            servers=[_Closable(events, "server", "stop")],
            database=_Closable(events, "database", "close"),
            timeout_ms=1,
        )

        self.assertTrue(failures)
        self.assertNotIn("client", events)
        self.assertNotIn("server", events)
        self.assertNotIn("database", events)


if __name__ == "__main__":
    unittest.main()
