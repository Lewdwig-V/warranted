"""Credential-free model fixture with a request witness outside the host stores.

Every POST is an effect, even a duplicate. The request log deliberately does not
deduplicate. A separate receipt log supports attributable read-only recovery.
"""

import argparse
import base64
import json
import socket
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from time import perf_counter_ns


def serve(root: Path, pipe=None):
    config = json.loads((root / "config.json").read_text())

    def append(name, value):
        with (root / name).open("a") as file:
            file.write(json.dumps(value, sort_keys=True) + "\n")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def send(self, code, body):
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):
            started = perf_counter_ns()
            size = int(self.headers.get("Content-Length", "0"))
            if self.path != "/attempt" or not 0 < size <= 2 * 1024 * 1024:
                self.send(400, {"error": "invalid request"})
                return
            request = json.loads(self.rfile.read(size))
            append("requests.jsonl", request)
            identity = request["identity"]
            index = int(identity["operation"].rsplit("/", 1)[1]) - 1
            if config["mode"] == "redirect":
                self.send_response(307)
                self.send_header("Location", "/attempt")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            response = config["responses"][min(index, len(config["responses"]) - 1)]
            usage = config["usage"][min(index, len(config["usage"]) - 1)]
            receipt = {
                "identity": {**identity, "service": config["service_id"]},
                "result": {
                    "outcome": config.get("outcome", "succeeded"),
                    "exit_code": config.get("exit_code", 0),
                    "usage": None
                    if config["mode"] == "unknown-usage"
                    else {"model": usage},
                    "elapsed_ns": perf_counter_ns() - started,
                },
                "channels": {"response": base64.b64encode(response.encode()).decode()},
            }
            if config["mode"] == "mismatched":
                receipt["identity"]["project"] = "another-project"
            if config["mode"] != "unprovable":
                append("receipts.jsonl", {"_lookup": identity["request"], **receipt})
            if config["drop_response"]:
                self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close()
            else:
                self.send(200, receipt)

        def do_GET(self):
            append("lookups.jsonl", {"path": self.path})
            path = root / "receipts.jsonl"
            receipts = (
                [json.loads(line) for line in path.read_text().splitlines()]
                if path.exists()
                else []
            )
            matching = [
                item for item in receipts if self.path == "/receipt/" + item["_lookup"]
            ]
            if len(matching) != 1:
                self.send(404, {"error": "no unique attributable receipt"})
                return
            self.send(
                200,
                {key: value for key, value in matching[0].items() if key != "_lookup"},
            )

    with HTTPServer(("127.0.0.1", 0), Handler) as server:
        url = f"http://127.0.0.1:{server.server_port}"
        if pipe is not None:
            pipe.send(url)
        else:
            (root / "url.txt").write_text(url)
            print(url, flush=True)
        server.serve_forever()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    serve(parser.parse_args().root)
