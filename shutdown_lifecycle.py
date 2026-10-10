"""Ordered shutdown for Qt workers and the local OCR runtime."""
import time


def _name(obj) -> str:
    return type(obj).__name__


def _unique(items):
    seen = set()
    for item in items:
        if item is not None and id(item) not in seen:
            seen.add(id(item))
            yield item


def _request_stop(worker) -> None:
    for method_name in ("cancel", "stop", "requestInterruption"):
        method = getattr(worker, method_name, None)
        if callable(method):
            method()
            return


def shutdown_runtime(
    workers=(), clients=(), models=(), servers=(), database=None, timeout_ms=30_000
):
    """Stop dependants before their HTTP, model, server and DB dependencies."""
    active = [w for w in _unique(workers) if w.isRunning()]
    failures = []
    for worker in active:
        try:
            _request_stop(worker)
        except Exception as exc:
            failures.append(f"worker:{_name(worker)} cancel: {exc}")

    deadline = time.monotonic() + timeout_ms / 1000
    for worker in active:
        remaining_ms = max(0, int((deadline - time.monotonic()) * 1000))
        try:
            worker.wait(remaining_ms)
        except Exception as exc:
            failures.append(f"worker:{_name(worker)} wait: {exc}")
        if worker.isRunning():
            failures.append(f"worker:{_name(worker)} still running")

    if any(item.startswith("worker:") for item in failures):
        return failures

    for group, method_name in ((clients, "close"), (models, "unload"), (servers, "stop")):
        for component in _unique(group):
            try:
                getattr(component, method_name)()
            except Exception as exc:
                failures.append(f"{method_name}:{_name(component)}: {exc}")

    if database is not None:
        try:
            database.close()
        except Exception as exc:
            failures.append(f"database:{_name(database)}: {exc}")
    return failures

