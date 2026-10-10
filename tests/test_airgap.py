import unittest
from pathlib import Path
from unittest.mock import patch

from tools.check_airgap import Connection, Inspection, audit, classify_connections


class AirGapClassificationTests(unittest.TestCase):
    @staticmethod
    def connection(remote, status="ESTABLISHED", local=("127.0.0.1", 50000)):
        return Connection(
            pid=42,
            process_name="python.exe",
            status=status,
            local_addr=local,
            remote_addr=remote,
        )

    def test_loopback_ipv4_ipv6_and_ipv4_mapped_ipv6_are_allowed(self):
        connections = [
            self.connection(("127.0.0.1", 9998)),
            self.connection(("::1", 9999)),
            self.connection(("::ffff:127.0.0.1", 9998)),
        ]

        self.assertEqual(classify_connections(connections), [])

    def test_listeners_and_unspecified_remote_addresses_are_not_outbound(self):
        connections = [
            self.connection(None, status="LISTEN", local=("0.0.0.0", 9998)),
            self.connection((), status="LISTEN", local=("::", 9999)),
            self.connection(("8.8.8.8", 443), status="LISTEN"),
            self.connection(("0.0.0.0", 0)),
            self.connection(("::", 0)),
        ]

        self.assertEqual(classify_connections(connections), [])

    def test_remote_non_loopback_connections_fail_even_when_not_established(self):
        remote = self.connection(("8.8.8.8", 443), status="SYN_SENT")
        ipv6 = self.connection(("2001:4860:4860::8888", 443))

        self.assertEqual(classify_connections([remote, ipv6]), [remote, ipv6])

    def test_malformed_remote_address_is_a_violation_not_a_silent_pass(self):
        malformed = self.connection(("not-an-ip", 443))

        self.assertEqual(classify_connections([malformed]), [malformed])


class AirGapReportTests(unittest.TestCase):
    def test_audit_exit_codes_distinguish_pass_fail_and_inspection_error(self):
        loopback = AirGapClassificationTests.connection(("127.0.0.1", 9998))
        remote = AirGapClassificationTests.connection(("1.1.1.1", 443))
        cases = [
            (Inspection([loopback], [42], []), ("PASS", 0)),
            (Inspection([remote], [42], []), ("FAIL", 1)),
            (Inspection([], [], ["access denied"]), ("ERROR", 2)),
        ]
        for inspection, expected in cases:
            with self.subTest(expected=expected), patch(
                "tools.check_airgap.collect_connections", return_value=inspection
            ):
                report = audit(Path("."))
                self.assertEqual((report["status"], report["exit_code"]), expected)


if __name__ == "__main__":
    unittest.main()
