import json
from email.message import Message
from io import BytesIO
from pathlib import Path
import tempfile
import time
import unittest

from acl_reference.api import _Handler
from acl_reference.usage_log import UsageLog


class UsageEventApiTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.log = UsageLog(Path(self.temporary.name) / "usage-logs")

    def tearDown(self):
        self.temporary.cleanup()

    def test_event_endpoint_joins_browser_sequence_with_server_identity(self):
        body = json.dumps({
            "event": "search",
            "resource": "AMBOS",
            "status": 200,
            "ms": 12.5,
            "query": "casa",
            "results": 2,
            "shown": ["DLP-casa", "VOLP-casa"],
        }).encode("utf-8")

        class DirectHandler(_Handler):
            def send_response(self, code, message=None):
                self.response_status = int(code)

            def send_header(self, name, value):
                return None

            def end_headers(self):
                return None

        handler = DirectHandler.__new__(DirectHandler)
        handler.path = "/api/usage-events"
        handler.client_address = ("127.0.0.1", 12345)
        handler.rfile = BytesIO(body)
        handler.wfile = BytesIO()
        handler.usage_log = self.log
        handler.headers = Message()
        for name, value in {
            "Content-Length": str(len(body)),
            "Content-Type": "application/json",
            "User-Agent": "browser-test",
            "X-ACL-Session": "session-test",
            "X-ACL-Seq": "7",
            "X-Forwarded-For": "192.0.2.44, 127.0.0.1",
        }.items():
            handler.headers[name] = value
        handler.do_POST()
        self.assertEqual(handler.response_status, 204)

        event_path = self.log._weekly_interaction_path()
        for _ in range(100):
            if event_path.is_file() and event_path.stat().st_size:
                break
            time.sleep(0.01)
        fields = event_path.read_text(encoding="utf-8").rstrip("\n").split("\t")
        self.assertEqual(len(fields), 19)
        self.assertEqual(fields[1:7], [
            "session-test", "7", "search", "192.0.2.44",
            "browser-test", "AMBOS",
        ])
        self.assertEqual(fields[9:12], [
            "casa", "2", "DLP-casa,VOLP-casa",
        ])


if __name__ == "__main__":
    unittest.main()
