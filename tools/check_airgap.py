"""Audit the portable app's TCP connections without changing network state.

By default only bundled ``python[.exe]`` and ``llama-server[.exe]`` processes
whose executable or working directory is inside the portable root are audited.
Pass one or more ``--pid`` values to audit an explicit app process tree.
"""

import argparse
import ipaddress
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple


Endpoint = Optional[Tuple[str, int]]
_APP_PROCESS_NAMES = {"python", "python.exe", "llama-server", "llama-server.exe"}


@dataclass(frozen=True)
class Connection:
    pid: int
    process_name: str
    status: str
    local_addr: Endpoint
    remote_addr: Endpoint


@dataclass
class Inspection:
    connections: List[Connection]
    process_ids: List[int]
    errors: List[str]


def _endpoint(value) -> Endpoint:
    if not value:
        return None
    host = getattr(value, "ip", None)
    port = getattr(value, "port", None)
    if host is None:
        try:
            host, port = value[0], value[1]
        except (IndexError, TypeError):
            return None
    try:
        port_number = int(port)
    except (TypeError, ValueError):
        port_number = 0
    return str(host), port_number


def _is_local_only(endpoint: Endpoint) -> bool:
    if not endpoint:
        return True
    host = endpoint[0].split("%", 1)[0]
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    return address.is_loopback or address.is_unspecified


def classify_connections(connections: Iterable[Connection]) -> List[Connection]:
    """Return outbound TCP connections whose peer is not local-only.

    This function is deliberately independent of psutil and operating-system
    state so the security rule can be exhaustively unit tested.
    """
    return [
        connection
        for connection in connections
        if connection.status.upper() != "LISTEN"
        and connection.remote_addr
        and not _is_local_only(connection.remote_addr)
    ]


def _inside(path_value: Optional[str], root: Path) -> bool:
    if not path_value:
        return False
    try:
        Path(path_value).resolve().relative_to(root)
        return True
    except (OSError, ValueError):
        return False


def _explicit_processes(psutil, root_pids: Sequence[int], errors: List[str]):
    found = {}
    for pid in root_pids:
        try:
            root = psutil.Process(pid)
            found[root.pid] = root
            for child in root.children(recursive=True):
                found[child.pid] = child
        except (psutil.NoSuchProcess, psutil.AccessDenied) as exc:
            errors.append("PID {}: {}".format(pid, exc))
    return list(found.values())


def _bundled_processes(psutil, app_root: Path):
    processes = []
    for process in psutil.process_iter(["pid", "name", "exe", "cwd"]):
        try:
            name = (process.info.get("name") or "").casefold()
            if name not in _APP_PROCESS_NAMES:
                continue
            if _inside(process.info.get("exe"), app_root) or _inside(
                process.info.get("cwd"), app_root
            ):
                processes.append(process)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return processes


def collect_connections(
    app_root: Path, root_pids: Optional[Sequence[int]] = None
) -> Inspection:
    """Collect TCP state for bundled app processes or an explicit PID tree."""
    try:
        import psutil
    except ImportError:
        return Inspection([], [], ["psutil is not installed"])

    root = Path(app_root).resolve()
    errors = []  # type: List[str]
    processes = (
        _explicit_processes(psutil, root_pids, errors)
        if root_pids
        else _bundled_processes(psutil, root)
    )
    connections = []  # type: List[Connection]
    inspected = []  # type: List[int]
    for process in processes:
        try:
            name = process.name()
            inspected.append(process.pid)
            for item in process.net_connections(kind="tcp"):
                connections.append(
                    Connection(
                        pid=process.pid,
                        process_name=name,
                        status=str(item.status or ""),
                        local_addr=_endpoint(item.laddr),
                        remote_addr=_endpoint(item.raddr),
                    )
                )
        except (psutil.NoSuchProcess, psutil.AccessDenied) as exc:
            errors.append("PID {}: {}".format(process.pid, exc))
    return Inspection(connections, sorted(set(inspected)), errors)


def _connection_dict(connection: Connection) -> dict:
    value = asdict(connection)
    value["local_addr"] = list(connection.local_addr) if connection.local_addr else None
    value["remote_addr"] = list(connection.remote_addr) if connection.remote_addr else None
    return value


def audit(app_root: Path, root_pids: Optional[Sequence[int]] = None) -> dict:
    inspection = collect_connections(app_root, root_pids)
    violations = classify_connections(inspection.connections)
    if violations:
        status, exit_code = "FAIL", 1
    elif inspection.errors:
        status, exit_code = "ERROR", 2
    else:
        status, exit_code = "PASS", 0
    return {
        "status": status,
        "exit_code": exit_code,
        "app_root": str(Path(app_root).resolve()),
        "process_ids": inspection.process_ids,
        "connections_checked": len(inspection.connections),
        "violations": [_connection_dict(item) for item in violations],
        "errors": inspection.errors,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Check that OCR VBHC processes only use local TCP connections."
    )
    parser.add_argument(
        "--app-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="portable application root (default: parent of tools)",
    )
    parser.add_argument(
        "--pid",
        type=int,
        action="append",
        dest="pids",
        help="audit this PID and all descendants; repeat for multiple roots",
    )
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = parser.parse_args(argv)
    report = audit(args.app_root, args.pids)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(
            "{status}: {connections_checked} TCP connection(s), "
            "{process_count} process(es), {violation_count} violation(s)".format(
                process_count=len(report["process_ids"]),
                violation_count=len(report["violations"]),
                **report
            )
        )
        for item in report["violations"]:
            print(
                "  PID {pid} {process_name}: {local_addr} -> {remote_addr} ({status})".format(
                    **item
                )
            )
        for error in report["errors"]:
            print("  ERROR: {}".format(error))
    return int(report["exit_code"])


if __name__ == "__main__":
    raise SystemExit(main())
