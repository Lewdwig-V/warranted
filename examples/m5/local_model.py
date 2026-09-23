"""Explicit local inference probe. It never executes the generated command."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from time import monotonic_ns
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

from warranted import chat_completions
from warranted.acceptance import _encode
from warranted.attempts import _NoRedirect
from warranted.chat_completions import MAX_BYTES, LocalChatCompletions
from warranted.ledger import Ledger, Manifest, Outcome, Result, Snapshot, _json_object
from warranted.worker import AttemptResult, Episode, Journal, WorkerModel

PROMPT = 'Return exactly this JSON object: {"command":"true"}. No other text.'
METADATA_REQUEST_TIMEOUT_SECONDS = 10


def runtime(client: LocalChatCompletions, raw: dict[str, bytes] | None = None) -> bytes:
    """Read Ollama metadata only; reject cloud-backed models before inference."""
    if raw is None:
        raw = {}
    opener = build_opener(ProxyHandler({}), _NoRedirect())

    def read(path, payload=None):
        request = Request(
            client.base_url.removesuffix("/v1") + path,
            data=None if payload is None else _encode(payload),
            headers={"Content-Type": "application/json"},
        )
        try:
            response = opener.open(request, timeout=METADATA_REQUEST_TIMEOUT_SECONDS)
        except HTTPError as error:
            response = error
        with response:
            raw[f"{path.lstrip('/')}-status.json"] = _encode(response.status)
            data = response.read(MAX_BYTES + 1)
            raw[f"{path.lstrip('/')}-response"] = data
        if len(data) > MAX_BYTES:
            raise ValueError("model metadata exceeds byte limit")
        if response.status != 200:
            raise ValueError(f"metadata HTTP status {response.status}")
        return json.loads(data, object_pairs_hook=_json_object)

    version = read("/api/version")
    models = [m for m in read("/api/tags")["models"] if m["name"] == client.model]
    if len(models) != 1:
        raise ValueError("model is not installed under the exact requested tag")
    model = models[0]
    show = read("/api/show", {"model": client.model})
    if (
        show.get("remote_host")
        or show.get("remote_model")
        or model.get("remote_host")
        or model.get("remote_model")
        or type(model.get("size")) is not int
        or model["size"] <= 0
        or not re.fullmatch(r"[0-9a-f]{64}", model.get("digest", ""))
    ):
        raise ValueError("probe requires installed local model files")
    return _encode({"version": version, "model": model, "show": show})


def runtime_failure(
    client: LocalChatCompletions, expected: bytes
) -> AttemptResult | None:
    """Settle metadata errors without treating an unsent inference as unknown."""
    started = monotonic_ns()
    raw = {}
    try:
        raw["runtime.json"] = runtime(client, raw)
        if raw["runtime.json"] != expected:
            raise ValueError("local model or server changed since initialization")
    except Exception as error:
        # This scope only reads metadata. Inference dispatch stays outside it.
        raw["diagnostic"] = f"{type(error).__name__}: {error}".encode()
        return AttemptResult(
            Result(
                Outcome.INFRASTRUCTURE_FAILURE,
                None,
                {"model": 0},
                monotonic_ns() - started,
            ),
            raw,
        )
    return None


def initialize(root: Path, client: LocalChatCompletions) -> None:
    metadata = runtime(client)
    with Ledger.create(
        root,
        Manifest(
            "m5-local-model-probe",
            "1",
            root.name,
            "world",
            {"api": "chat-completions", "scope": "development-probe"},
            {"model": 1},
        ),
        {
            "model-api": client.snapshot,
            "runtime": Snapshot(metadata, client.base_url, "ollama-metadata-v1"),
            "prompt": Snapshot(PROMPT.encode(), "m5-local-model-probe", "1"),
            "chat-completions.py": Snapshot(
                Path(chat_completions.__file__).read_bytes(),
                "m5/model-adapter/chat-completions.py",
                "1",
            ),
            "local-model.py": Snapshot(
                Path(__file__).read_bytes(), "m5/model-adapter/local-model.py", "1"
            ),
        },
    ):
        pass


def run(root: Path) -> dict:
    with Ledger.open(root) as ledger:
        for name, source in (
            ("chat-completions.py", Path(chat_completions.__file__)),
            ("local-model.py", Path(__file__)),
        ):
            if (
                ledger.read_artifact(ledger.project.snapshots[name].artifact)
                != source.read_bytes()
            ):
                raise ValueError("model adapter changed since initialization")
        config = json.loads(
            ledger.read_artifact(ledger.project.snapshots["model-api"].artifact)
        )
        parameters = config["parameters"]
        client = LocalChatCompletions(
            config["base_url"],
            parameters["model"],
            parameters["max_tokens"],
            config["timeout_seconds"],
            parameters["seed"],
        )
        if client.snapshot.data != _encode(config):
            raise ValueError("unsupported model configuration")
        prompt = ledger.read_artifact(
            ledger.project.snapshots["prompt"].artifact
        ).decode()
        episode = Episode(
            "probe",
            prompt,
            (),
            model=client.model,
            model_service=client.service_id,
            max_steps=1,
        )

        def attempt(request, payload):
            expected = ledger.read_artifact(
                ledger.project.snapshots["runtime"].artifact
            )
            failure = runtime_failure(client, expected)
            if failure is not None:
                return failure
            return client(request, payload)

        # Completed responses bypass the boundary, including all metadata reads.
        result = WorkerModel(Journal(ledger, episode), attempt).query(
            [{"role": "user", "content": prompt}]
        )
        return {"command": json.loads(result["content"])["command"], "executed": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    init = sub.add_parser("init", help="capture local metadata without inference")
    init.add_argument("root", type=Path)
    init.add_argument("--base-url", default="http://127.0.0.1:11434/v1")
    init.add_argument("--model", required=True)
    init.add_argument("--max-tokens", type=int, default=64)
    init.add_argument("--timeout", type=int, default=120)
    resume = sub.add_parser("run", help="make or reuse the single recorded attempt")
    resume.add_argument("root", type=Path)
    args = parser.parse_args()
    if args.action == "init":
        initialize(
            args.root,
            LocalChatCompletions(
                args.base_url, args.model, args.max_tokens, args.timeout
            ),
        )
        print("Metadata captured. No inference requested.")
    else:
        print(json.dumps(run(args.root), sort_keys=True))


if __name__ == "__main__":
    main()
