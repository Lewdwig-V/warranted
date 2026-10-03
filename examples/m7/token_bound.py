"""Measure a local model's prompt tokens against the adapter's reservation bound.

A model can join `LocalChatCompletions.verified_models` only after a recorded
measurement shows that its reported prompt tokens never exceed the bound the
adapter would reserve. This script sends a fixed set of requests, including
short and token-dense prompts, to a pinned installed model and writes a report
with the raw request and response bytes.

    uv run --locked python examples/m7/token_bound.py measure out/ \\
        --model qwen3:8b --base-url http://127.0.0.1:11434/v1

The report is evidence for a reviewer; adding the printed key to
`verified_models` is a separate, reviewed change with the report recorded under
`docs/experiments/`. Each request is one inference with a small `max_tokens`.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sys
from datetime import UTC, datetime
from pathlib import Path

from warranted.acceptance import _encode
from warranted.chat_completions import (
    MAX_BYTES,
    MESSAGE_MARGIN,
    LocalChatCompletions,
    http_response,
)
from warranted.ledger import _json_object

PROSE = (
    "The harness records every model request and tool result before the worker "
    "reads it, so that later checks can cite exact bytes. "
)
# Each case stresses a different way prompt tokens can approach the byte length:
# template overhead on short messages, digits and symbols that tokenize finely,
# non-ASCII text, and many tiny messages.
CASES = {
    "empty": [{"role": "user", "content": ""}],
    "one-character": [{"role": "user", "content": "a"}],
    "short": [{"role": "user", "content": "Return a JSON command."}],
    "system-and-user": [
        {"role": "system", "content": "You are a careful shell assistant."},
        {"role": "user", "content": "Return a JSON command."},
    ],
    "digits": [{"role": "user", "content": "1234567890" * 60}],
    "symbols": [{"role": "user", "content": "{}[]()<>;:|&$#@!%^*~`" * 30}],
    "whitespace": [{"role": "user", "content": " \n\t" * 200 + "end"}],
    "non-ascii": [
        {
            "role": "user",
            "content": "日本語のテキスト、ü é ñ ß, emoji 😀🚀, Ελληνικά. " * 20,
        }
    ],
    "many-tiny-messages": [
        {"role": "user" if i % 2 == 0 else "assistant", "content": "x"}
        for i in range(41)
    ],
    "prose": [{"role": "user", "content": PROSE * 60}],
}


def installed(client: LocalChatCompletions) -> dict:
    """The tag's installed metadata; the digest pins what was measured."""
    with http_response(
        client.base_url.removesuffix("/v1") + "/api/tags", None, 10
    ) as response:
        body, status = response.read(MAX_BYTES + 1), response.status
    if status != 200 or len(body) > MAX_BYTES:
        raise ValueError(f"model metadata HTTP status {status}")
    models = json.loads(body, object_pairs_hook=_json_object)["models"]
    matches = [m for m in models if m.get("name") == client.model]
    if len(matches) != 1 or "digest" not in matches[0]:
        raise ValueError("model is not installed under the exact requested tag")
    return matches[0]


def measure(client: LocalChatCompletions, out: Path) -> dict:
    model = installed(client)
    pinned = type(
        "Measured",
        (LocalChatCompletions,),
        {"verified_models": frozenset({f"{client.model}@{model['digest']}"})},
    )(
        client.base_url,
        client.model,
        client.max_tokens,
        client.timeout_seconds,
        client.seed,
        model["digest"],
    )
    raw = out / "raw"
    raw.mkdir(parents=True)
    rows = []
    for name, messages in CASES.items():
        payload = _encode({"messages": messages})
        wire, _ = pinned._wire(payload)
        bound = pinned.reservation(payload)["prompt_tokens"]
        status, body = pinned._post(wire)
        (raw / f"{name}.request.json").write_bytes(wire)
        (raw / f"{name}.response").write_bytes(body)
        usage = None
        if status == 200:
            usage = json.loads(body, object_pairs_hook=_json_object).get("usage")
        prompt = usage.get("prompt_tokens") if type(usage) is dict else None
        rows.append(
            {
                "case": name,
                "messages": len(messages),
                "wire_bytes": len(wire),
                "bound": bound,
                "prompt_tokens": prompt,
                "status": status,
                "within_bound": type(prompt) is int and prompt <= bound,
                "response_sha256": hashlib.sha256(body).hexdigest(),
            }
        )
    passed = all(row["within_bound"] for row in rows)
    report = {
        "measured_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "host": platform.platform(),
        "adapter": json.loads(pinned.snapshot.data),
        "installed_model": model,
        "message_margin": MESSAGE_MARGIN,
        "verification_key": pinned.verification_key,
        "passed": passed,
        "least_headroom": min(
            (
                row["bound"] - row["prompt_tokens"]
                for row in rows
                if row["within_bound"]
            ),
            default=None,
        ),
        "cases": rows,
    }
    (out / "report.json").write_bytes(_encode(report))
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="action", required=True)
    run = sub.add_parser("measure", help="send the fixed cases and write a report")
    run.add_argument("out", type=Path, help="new directory for the report")
    run.add_argument("--base-url", default="http://127.0.0.1:11434/v1")
    run.add_argument("--model", required=True)
    run.add_argument("--max-tokens", type=int, default=16)
    run.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args(argv)
    if args.out.exists():
        parser.error("the output directory must not exist")
    client = LocalChatCompletions(
        args.base_url, args.model, args.max_tokens, args.timeout
    )
    report = measure(client, args.out)
    for row in report["cases"]:
        print(
            f"{row['case']:<20} bound {row['bound']:>7}  "
            f"prompt tokens {row['prompt_tokens']!s:>7}  "
            f"{'ok' if row['within_bound'] else 'EXCEEDS OR UNMEASURED'}"
        )
    if report["passed"]:
        print(f"Within bound. Verification key: {report['verification_key']}")
        return 0
    print("Not verified: at least one case exceeded the bound or was unmeasured.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
