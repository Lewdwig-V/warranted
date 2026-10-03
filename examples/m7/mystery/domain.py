"""Prototype ReSchema-style domain: model a black-box program, checked on hidden inputs.

The checker replays the worker's nominated cases and fresh hidden cases through the
original program and the worker's model, each in its own contained job. Feedback
reveals the first divergence on a nominated case, but only counts for hidden cases.
"""

import base64
import json
import random

from warranted import (
    DEFAULT_WORKER_IMAGE,
    CheckContext,
    OperationContext,
    OperationResult,
    Outcome,
    Verdict,
    VerdictStatus,
)

MAX_CASES, MAX_CASE_LENGTH, CASE_SECONDS = 16, 256, 2

# Runs inside a job, beside one program. The program can only shape its own results.
HARNESS = """
import base64, json, subprocess, sys
results = []
for case in json.load(open('cases.json')):
    try:
        done = subprocess.run([sys.executable, '-I', 'prog.py'], input=case.encode(),
                              capture_output=True, timeout={seconds})
        results.append([done.returncode, base64.b64encode(done.stdout).decode()])
    except subprocess.TimeoutExpired:
        results.append([None, ''])
print(json.dumps(results))
""".replace("{seconds}", str(CASE_SECONDS))


class Replay:
    version = "1"
    isolated = True  # contained jobs and host-recorded entropy; no shared state

    def _run(self, ctx: CheckContext, source: bytes, cases: list[str]):
        result = ctx.run_job(
            DEFAULT_WORKER_IMAGE,
            ["python", "-I", "harness.py"],
            {
                "prog.py": source,
                "harness.py": HARNESS.encode(),
                "cases.json": json.dumps(cases).encode(),
            },
            timeout_seconds=CASE_SECONDS * len(cases) + 10,
        )
        if result.returncode != 0 or result.timed_out or result.truncated:
            return None
        try:
            rows = json.loads(result.stdout)
            if type(rows) is not list or len(rows) != len(cases):
                return None
            return [
                None if code is None else (code, base64.b64decode(out, validate=True))
                for code, out in rows
            ]
        except (ValueError, TypeError):
            return None

    def check(self, ctx: CheckContext) -> Verdict:
        try:
            payload = json.loads(ctx.candidate["result.json"])
        except KeyError:
            return Verdict(VerdictStatus.REJECTED, {"error": "result.json is missing"})
        except (ValueError, RecursionError):
            return Verdict(VerdictStatus.REJECTED, {"error": "result.json is not JSON"})
        if (
            type(payload) is not dict
            or set(payload) != {"model", "cases"}
            or type(payload["model"]) is not str
            or type(payload["cases"]) is not list
            or len(payload["cases"]) > MAX_CASES
            or not all(
                type(c) is str and len(c) <= MAX_CASE_LENGTH for c in payload["cases"]
            )
        ):
            return Verdict(
                VerdictStatus.REJECTED,
                {"error": "result.json needs a model string and up to 16 short cases"},
            )
        nominated = list(dict.fromkeys(payload["cases"]))
        config = json.loads(ctx.private["hidden.json"])
        rng = random.Random(ctx.draw_seed())
        hidden = []
        while len(hidden) < config["count"]:
            length = rng.randint(0, config["max_length"])
            case = "".join(rng.choice(config["alphabet"]) for _ in range(length))
            if case not in nominated and case not in hidden:
                hidden.append(case)
        cases = nominated + hidden
        expected = self._run(ctx, ctx.inputs["mystery.py"], cases)
        if expected is None or any(item is None or item[0] for item in expected):
            raise RuntimeError("the original program failed on a checker case")
        actual = self._run(ctx, payload["model"].encode(), cases)
        if actual is None:
            return Verdict(
                VerdictStatus.REJECTED,
                {"error": "the model's results could not be read"},
            )
        first, hidden_failed = None, 0
        for index, case in enumerate(cases):
            if actual[index] == expected[index]:
                continue
            if index < len(nominated):
                if first is None:
                    first = {
                        "case": case,
                        "expected": expected[index][1].decode(errors="replace"),
                        "actual": _describe(actual[index]),
                    }
            else:
                hidden_failed += 1
        status = (
            VerdictStatus.PASSED
            if first is None and not hidden_failed
            else VerdictStatus.REJECTED
        )
        feedback = {
            "nominated": {"checked": len(nominated), "first_divergence": first},
            "hidden": {"checked": len(hidden), "failed": hidden_failed},
        }
        return Verdict(status, feedback, {"hidden_cases": hidden})


def _describe(item) -> str:
    if item is None:
        return "timed out"
    code, out = item
    text = out.decode(errors="replace")
    return text if code == 0 else f"exited {code}: {text}"


class Probe:
    """Run the original program on one worker-chosen input, observed by the host."""

    version = "1"
    effect = "none"  # a contained job; nothing outside the host changes
    shared = False
    reusable = True  # the program is deterministic, so equal inputs give equal output
    inputs = ("mystery.py",)
    units = frozenset({"probe"})

    def parse(self, arguments):
        if (
            type(arguments) is not dict
            or set(arguments) != {"input"}
            or type(arguments["input"]) is not str
            or len(arguments["input"]) > MAX_CASE_LENGTH
        ):
            raise ValueError("probe needs one input string of at most 256 characters")
        return {"input": arguments["input"]}

    def reservation(self, arguments):
        return {"probe": 1}

    def execute(self, ctx: OperationContext, arguments) -> OperationResult:
        result = ctx.run_job(
            DEFAULT_WORKER_IMAGE,
            ["python", "-I", "prog.py"],
            {"prog.py": ctx.inputs["mystery.py"]},
            stdin=arguments["input"].encode(),
            timeout_seconds=CASE_SECONDS + 5,
        )
        if result.timed_out:
            return OperationResult(
                Outcome.FAILED, {"probe": 1}, shown={"timed_out": True}
            )
        shown = {
            "exit": result.returncode,
            "output": result.stdout.decode(errors="replace")[:4096],
        }
        outcome = Outcome.SUCCEEDED if result.returncode == 0 else Outcome.FAILED
        return OperationResult(outcome, {"probe": 1}, shown=shown)


class MysteryDomain:
    name = "mystery-replay"
    version = "0.1"
    worker_image = DEFAULT_WORKER_IMAGE
    checkers = {"replay": Replay()}
    operations = {"probe": Probe()}
