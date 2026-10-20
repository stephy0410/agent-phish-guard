import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from guardrails.decider import DeciderClient, DeciderUnavailable


class FakeDecider(BaseHTTPRequestHandler):
    requests: list[dict] = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["content-length"])))
        FakeDecider.requests.append(body)
        self._reply(
            {
                "model": "fake",
                "answers": {"q": {"type": "noul", "noul": 0.9}},
                "usage": {"input_tokens": 42, "output_tokens": 1},
                "latency_ms": 12.5,
            }
        )

    def do_GET(self):
        self._reply({"status": "ok", "device": "cpu"})

    def _reply(self, payload):
        data = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


@pytest.fixture
def server_url():
    server = HTTPServer(("127.0.0.1", 0), FakeDecider)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def test_ask_sends_state_and_questions(server_url):
    question = DeciderClient.noul("Is it urgent?", {"true": "urgent", "false": "not urgent"})
    response = DeciderClient(server_url).ask("help!", {"q": question})

    assert FakeDecider.requests[-1] == {
        "state": "help!",
        "questions": {
            "q": {
                "type": "noul",
                "instructions": "Is it urgent?",
                "criteria": {"true": "urgent", "false": "not urgent"},
            }
        },
    }
    assert response.answers["q"]["noul"] == 0.9
    assert response.input_tokens == 42
    assert response.latency_ms == 12.5
    assert response.model == "fake"


def test_health(server_url):
    assert DeciderClient(server_url).health()["device"] == "cpu"


def test_url_from_env(monkeypatch):
    monkeypatch.setenv("DECIDER_URL", "http://decider.internal:9000/")
    assert DeciderClient().url == "http://decider.internal:9000"


def test_unavailable_server_names_the_fix():
    with pytest.raises(DeciderUnavailable, match="strands-decider serve"):
        DeciderClient("http://127.0.0.1:9", timeout=1).health()
