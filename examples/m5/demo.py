"""Assess three fixed repository migrations under two independent contracts."""

import argparse
import base64
import hashlib
import json
import os
import platform
from dataclasses import asdict
from functools import partial
from pathlib import Path
from time import perf_counter_ns
from uuid import uuid4

os.environ["MSWEA_SILENT_STARTUP"] = "1"

from warranted import acceptance, containers, sandbox  # noqa: E402
from warranted.acceptance import (  # noqa: E402
    Acceptance,
    AcceptanceContext,
    Evidence,
    _digest,
    _encode,
)
from warranted.containers import SandboxFailure, _run  # noqa: E402
from warranted.exports import export_evidence  # noqa: E402
from warranted.ledger import (  # noqa: E402
    Ledger,
    Manifest,
    Origin,
    Outcome,
    Request,
    Result,
    Snapshot,
    _json_object,
)
from warranted.sandbox import SANDBOX_ID, Sandbox  # noqa: E402
from warranted.worker import (  # noqa: E402
    AttemptResult,
    Episode,
    UnknownOutcome,
    unresolved,
)

HERE = Path(__file__).resolve().parent
SOURCE_LIMIT = 64 * 1024
OUTPUT_LIMIT = 64 * 1024
CASE_SECONDS = 2
NAMES = ("one-way", "drop-label", "complete")

# One root supervisor per candidate. Only the current input reaches each fresh
# worker process; expected answers never enter the container.
SUPERVISE = """
import base64, json, os, resource, subprocess, sys, tempfile, time
from pathlib import Path
limit = int(sys.argv[3])
def limits():
    resource.setrlimit(resource.RLIMIT_FSIZE, (limit + 1, limit + 1))
def stop_workers():
    try:
        os.kill(-1, 9)
    except ProcessLookupError:
        pass
    for _ in range(1000):
        live = False
        for path in Path('/proc').glob('[0-9]*/status'):
            try:
                fields = dict(line.split(':', 1)
                              for line in path.read_text().splitlines())
            except FileNotFoundError:
                continue
            if fields['Uid'].split()[0] == '1000' and fields['State'].strip()[0] != 'Z':
                live = True
        if not live:
            return
        time.sleep(0.001)
    raise RuntimeError('worker processes did not stop')
results = {}
for name, data in json.load(sys.stdin).items():
    started = time.perf_counter_ns()
    with tempfile.TemporaryDirectory(dir='/work') as work, \\
         tempfile.TemporaryFile(dir='/tmp/warranted-host') as out, \\
         tempfile.TemporaryFile(dir='/tmp/warranted-host') as err:
        os.chmod(work, 0o777)
        status = {'returncode': None, 'timed_out': False}
        try:
            result = subprocess.run([sys.executable, '-I', '/work/' + sys.argv[1]],
                                    input=data.encode(), stdout=out, stderr=err,
                                    cwd=work, user=1000, group=1000, extra_groups=[],
                                    preexec_fn=limits, timeout=int(sys.argv[2]))
            status['returncode'] = result.returncode
        except subprocess.TimeoutExpired:
            status['timed_out'] = True
        finally:
            stop_workers()
        out.seek(0)
        err.seek(0)
        stdout, stderr = out.read(limit + 1), err.read(limit + 1)
        status['output_limited'] = len(stdout) + len(stderr) > limit
        stdout = stdout[:limit]
        stderr = stderr[:limit - len(stdout)]
        status['elapsed_ns'] = time.perf_counter_ns() - started
        results[name] = dict(status, stdout=base64.b64encode(stdout).decode(),
                            stderr=base64.b64encode(stderr).decode())
print(json.dumps(results))
"""


def decode(data: bytes):
    def invalid(value):
        raise ValueError(f"invalid JSON constant: {value}")

    return json.loads(data, object_pairs_hook=_json_object, parse_constant=invalid)


def candidate_source(payload: bytes) -> bytes:
    if len(payload) > SOURCE_LIMIT * 8:
        raise ValueError("candidate payload exceeds limit")
    value = decode(payload)
    if (
        type(value) is not dict
        or set(value) != {"migrate.py"}
        or type(value["migrate.py"]) is not str
    ):
        raise ValueError("candidate must change only migrate.py")
    source = value["migrate.py"].encode()
    if not source or len(source) > SOURCE_LIMIT:
        raise ValueError("candidate source exceeds limit or is empty")
    return source


def valid_output(value) -> bool:
    required = {"version", "endpoint", "timeout_seconds"}
    return (
        type(value) is dict
        and required <= value.keys() <= required | {"label"}
        and type(value["version"]) is int
        and value["version"] == 2
        and type(value["endpoint"]) is str
        and bool(value["endpoint"])
        and type(value["timeout_seconds"]) is int
        and value["timeout_seconds"] >= 0
        and ("label" not in value or type(value["label"]) is str)
    )


def judge(case: dict, output: bytes, returncode: int | None, timed_out: bool) -> dict:
    if case["kind"] == "invalid":
        return {
            "input_rejection": not timed_out
            and returncode is not None
            and returncode > 0
            and not output
        }
    value = None
    if returncode == 0 and not timed_out and len(output) <= OUTPUT_LIMIT:
        try:
            value = decode(output)
        except (ValueError, RecursionError):
            pass
    expected = case["expected"]
    if case["kind"] == "repeat":
        return {"repetition": valid_output(value) and value == expected}
    if case["kind"] != "migrate":
        raise ValueError("unknown case kind")
    is_object = type(value) is dict
    return {
        "output_schema": valid_output(value),
        "renaming": is_object
        and type(value.get("version")) is int
        and value["version"] == 2
        and value.get("endpoint") == expected["endpoint"]
        and type(value.get("timeout_seconds")) is int
        and value["timeout_seconds"] == expected["timeout_seconds"]
        and not {"host", "timeout"} & value.keys(),
        "legacy_label": is_object
        and ("label" in value) == ("label" in expected)
        and value.get("label", "default") == case["legacy"],
    }


def snapshots() -> dict[str, Snapshot]:
    fixture = HERE / "fixture"
    captured = {
        str(path.relative_to(fixture)): Snapshot(
            path.read_bytes(), "m5/fixture/" + str(path.relative_to(fixture)), "1"
        )
        for path in sorted(fixture.rglob("*"))
        if path.is_file() and "__pycache__" not in path.parts
    }
    for name, path in (
        ("host.py", Path(__file__)),
        ("sandbox.py", Path(sandbox.__file__)),
        ("containers.py", Path(containers.__file__)),
        ("acceptance.py", Path(acceptance.__file__)),
    ):
        captured[name] = Snapshot(path.read_bytes(), "m5/checker/" + name, "1")
    base = {
        name.removeprefix("seed/"): hashlib.sha256(value.data).hexdigest()
        for name, value in captured.items()
        if name.startswith("seed/")
    }
    captured["repository.json"] = Snapshot(
        _encode({"version": 1, "revision": _digest(base), "files": base}),
        "m5/seed-manifest",
        "1",
    )
    complete = captured["complete.py"].data
    for name in NAMES:
        source = complete
        if name == "one-way":
            source = source.replace(b"ACCEPT_CURRENT = True", b"ACCEPT_CURRENT = False")
        if name == "drop-label":
            source = source.replace(b"KEEP_LABEL = True", b"KEEP_LABEL = False")
        payload = _encode({"migrate.py": source.decode()})
        captured[f"candidate-{name}.json"] = Snapshot(payload, "m5/" + name, "1")
        captured[f"candidate-{name}.py"] = Snapshot(
            candidate_source(payload), "m5/" + name + "/migrate.py", "1"
        )
    for name, case in decode(captured["references.json"].data).items():
        captured[f"case-{name}.json"] = Snapshot(
            case["input"].encode(), "m5/references#" + name, "1"
        )
    contracts = decode(captured["contracts.json"].data)
    for version in ("initial", "revised"):
        policy = {
            "version": 1,
            "owner": contracts["owner"],
            "transition": "accept-candidate",
            "requirements": {
                field: {"kind": "gate", "check": "evaluate", "field": field}
                for field in contracts[version]
            },
        }
        captured[f"policy-{version}.json"] = Snapshot(
            _encode(policy), "m5/contracts#" + version, "1"
        )
    return captured


def environment() -> dict:
    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "container": SANDBOX_ID,
        "model": "none",
        "policy": "fixed-m5-matrix-v1",
        "evaluator": "migration-v1",
        "split": "development",
        "case_seconds": str(CASE_SECONDS),
        "output_limit": str(OUTPUT_LIMIT),
    }


def initialize(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    captured = snapshots()
    with Ledger.create(
        root / "ledger",
        Manifest(
            "m5-migration",
            "1",
            str(uuid4()),
            str(uuid4()),
            environment(),
            {"batch": len(NAMES), "check": len(NAMES) * 2},
        ),
        captured,
    ):
        pass


def validate(ledger: Ledger):
    current = snapshots()
    if (
        ledger.project.manifest.environment != environment()
        or current.keys() != ledger.project.snapshots.keys()
    ):
        raise ValueError("fixture or environment changed")
    for name, value in current.items():
        ref = ledger.project.snapshots[name]
        if (
            ref.origin != value.origin
            or ref.version != value.version
            or ledger.read_artifact(ref.artifact) != value.data
        ):
            raise ValueError("fixture or checker changed: " + name)


def execute(root: Path, episode: Episode, inputs: dict[str, str]) -> AttemptResult:
    """One contained batch; its trusted supervisor captures each process result."""
    started = perf_counter_ns()
    with Sandbox(root / "ledger", episode) as isolated:
        try:
            isolated._prepare(True)
            completed = _run(
                [
                    "exec",
                    "--interactive",
                    "--user=0:0",
                    isolated.name,
                    "python",
                    "-I",
                    "-c",
                    SUPERVISE,
                    *episode.inputs,
                    str(CASE_SECONDS),
                    str(OUTPUT_LIMIT),
                ],
                _encode(inputs),
                seconds=len(inputs) * CASE_SECONDS + 5,
                output_limit=len(inputs) * (OUTPUT_LIMIT * 2 + 1024),
            )
            if completed.returncode:
                raise SandboxFailure(
                    "supervisor failed", completed.stdout, completed.stderr
                )
            results = decode(completed.stdout)
            if set(results) != set(inputs):
                raise SandboxFailure("incomplete supervisor result")
            raw = {"supervisor.stderr": completed.stderr}
            for name, value in results.items():
                out = base64.b64decode(value.pop("stdout"), validate=True)
                err = base64.b64decode(value.pop("stderr"), validate=True)
                if (
                    set(value)
                    != {"returncode", "timed_out", "output_limited", "elapsed_ns"}
                    or type(value["timed_out"]) is not bool
                    or type(value["output_limited"]) is not bool
                    or type(value["elapsed_ns"]) is not int
                    or value["elapsed_ns"] < 0
                    or (value["timed_out"] and value["returncode"] is not None)
                    or (not value["timed_out"] and type(value["returncode"]) is not int)
                    or len(out) + len(err) > OUTPUT_LIMIT
                ):
                    raise SandboxFailure("invalid supervisor result")
                raw[name + "/stdout"] = out
                raw[name + "/stderr"] = err
                raw[name + "/execution.json"] = _encode(value)
            code, outcome = 0, Outcome.SUCCEEDED
        except (SandboxFailure, OSError) as error:
            code, outcome = None, Outcome.INFRASTRUCTURE_FAILURE
            raw = {
                "stdout": getattr(error, "stdout", b""),
                "stderr": getattr(error, "stderr", b""),
                "diagnostic": str(error).encode(),
            }
    # A cleanup exception escapes before completion, retaining the reservation.
    return AttemptResult(
        Result(outcome, code, {"batch": 1}, perf_counter_ns() - started), raw
    )


def request(ledger: Ledger, kind: str, names: tuple[str, ...], extra=None) -> Request:
    inputs = {name: ledger.project.snapshots[name].artifact for name in names}
    inputs.update(extra or {})
    return Request(
        Origin(kind + "/" + _digest(inputs), kind, "m5-fixture", "1", inputs),
        ledger.project,
    )


def perform(ledger: Ledger, session: str, req: Request, unit: str, run):
    operation = ledger.reserve(session, req, {unit: 1})
    if operation.completion is None:
        unresolved(ledger)
        if not ledger.begin(session, req):
            raise UnknownOutcome(req.origin.operation_id)
        root = ledger.root.parent
        with (root / "executions.jsonl").open("ab") as witness:
            witness.write(
                _encode({"operation": req.origin.operation_id, "unit": unit}) + b"\n"
            )
        response = run()
        return ledger.complete(session, req, response.result, response.raw)
    ledger.lookup(req)
    for ref in operation.completion.observation.artifacts.values():
        ledger.read_artifact(ref)
    return operation.completion


def evaluate(ledger, cases, completion, integrity, fields) -> AttemptResult:
    started = perf_counter_ns()
    checks = {"repository_integrity": integrity}
    detail = {}
    infrastructure = False
    raw = {
        key: ledger.read_artifact(ref)
        for key, ref in completion.observation.artifacts.items()
    }
    for name, case in cases.items():
        state = (
            decode(raw[name + "/execution.json"])
            if name + "/execution.json" in raw
            else {"returncode": None, "timed_out": False, "output_limited": False}
        )
        values = judge(
            case,
            raw.get(name + "/stdout", b""),
            state["returncode"],
            state["timed_out"] or state["output_limited"],
        )
        if set(values) & set(fields):
            infrastructure |= (
                completion.result.outcome is Outcome.INFRASTRUCTURE_FAILURE
            )
        for field, passed in values.items():
            checks[field] = checks.get(field, True) and passed
        detail[name] = {"checks": values, **state}
    return AttemptResult(
        Result(
            Outcome.INFRASTRUCTURE_FAILURE if infrastructure else Outcome.SUCCEEDED,
            None if infrastructure else 0,
            {"check": 1},
            perf_counter_ns() - started,
        ),
        {
            "result.json": _encode({field: checks[field] for field in fields}),
            "all-checks.json": _encode(checks),
            "cases.json": _encode(detail),
        },
    )


def resolve(target, context, candidate):
    if candidate != target:
        raise ValueError("acceptance target changed")
    return context


def demonstrate(root: Path) -> dict:
    started = perf_counter_ns()
    with Ledger.open(root / "ledger") as ledger:
        validate(ledger)
        unresolved(ledger)
        session = ledger.start_session()
        before = len(ledger.operations())
        cases = decode(
            ledger.read_artifact(ledger.project.snapshots["references.json"].artifact)
        )
        matrix = {}
        for name in NAMES:
            target_name = f"candidate-{name}.json"
            source_name = f"candidate-{name}.py"
            target = Evidence(
                target_name, ledger.project.snapshots[target_name].artifact
            )
            source = candidate_source(ledger.read_artifact(target.artifact))
            integrity = source == ledger.read_artifact(
                ledger.project.snapshots[source_name].artifact
            )
            req = request(
                ledger,
                "migration-batch",
                (
                    target_name,
                    source_name,
                    "host.py",
                    "sandbox.py",
                    "containers.py",
                    *(f"case-{key}.json" for key in cases),
                ),
            )
            episode = Episode(
                name,
                "Execute the migration cases.",
                (source_name,),
                environment=SANDBOX_ID,
            )
            inputs = {
                key: ledger.read_artifact(
                    ledger.project.snapshots[f"case-{key}.json"].artifact
                ).decode()
                for key in cases
            }
            batch = perform(
                ledger, session, req, "batch", partial(execute, root, episode, inputs)
            )
            evidence = {
                Evidence.captured(batch.observation, channel).name: ref
                for channel, ref in batch.observation.artifacts.items()
            }
            matrix[name] = {"contracts": {}}
            for version in ("initial", "revised"):
                policy_name = f"policy-{version}.json"
                policy = Evidence(
                    policy_name, ledger.project.snapshots[policy_name].artifact
                )
                revision = Evidence(
                    "contracts.json",
                    ledger.project.snapshots["contracts.json"].artifact,
                )
                fields = decode(ledger.read_artifact(policy.artifact))["requirements"]
                req = request(
                    ledger,
                    "migration-check",
                    (
                        target_name,
                        policy_name,
                        "contracts.json",
                        "references.json",
                        "repository.json",
                        "host.py",
                    ),
                    evidence,
                )

                result = perform(
                    ledger,
                    session,
                    req,
                    "check",
                    partial(evaluate, ledger, cases, batch, integrity, fields),
                )
                context = AcceptanceContext(policy, revision, {"evaluate": req})
                boundary = Acceptance(
                    ledger,
                    session,
                    partial(resolve, target, context),
                )
                decision = boundary.accept(
                    target, {"evaluate": req.origin.operation_id}
                )
                matrix[name]["contracts"][version] = {
                    "status": decision.status,
                    "requirements": dict(decision.requirements),
                    "receipt": asdict(
                        Evidence.captured(
                            decision.completion.observation, "decision.json"
                        )
                    ),
                }
                for channel, key in (
                    ("all-checks.json", "checks"),
                    ("cases.json", "cases"),
                ):
                    matrix[name][key] = decode(
                        ledger.read_artifact(result.observation.artifacts[channel])
                    )
        balances = ledger.accounting()
        (root / "exports").mkdir(exist_ok=True)
        export = export_evidence(
            ledger,
            root / "exports" / session,
            snapshots=tuple(ledger.project.snapshots),
            observations={o.sequence: tuple(o.artifacts) for o in ledger.history()},
            operations=tuple(
                op.request.origin.operation_id for op in ledger.operations()
            ),
        )
        report = {
            "session": session,
            "project_id": ledger.project.project_id,
            "environment": environment(),
            "matrix": matrix,
            "spent": {unit: balance.spent for unit, balance in balances.items()},
            "reserved": {unit: balance.reserved for unit, balance in balances.items()},
            "new_operations": len(ledger.operations()) - before,
            "executions": len((root / "executions.jsonl").read_bytes().splitlines()),
            "execution_elapsed_ns": {
                kind: sum(
                    op.completion.result.elapsed_ns
                    for op in ledger.operations()
                    if op.request.origin.kind == kind and op.completion
                )
                for kind in ("migration-batch", "migration-check")
            },
            "elapsed_ns": perf_counter_ns() - started,
            "export": str(export),
        }
        (root / "reports").mkdir(exist_ok=True)
        (root / "reports" / (session + ".json")).write_bytes(_encode(report))
        (root / "reports/latest.json").write_bytes(_encode(report))
        return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    if not (root / "ledger").exists():
        initialize(root)
    report = demonstrate(root)
    print(json.dumps(report, indent=2))
    expected = {
        "one-way": ("accepted", "rejected"),
        "drop-label": ("rejected", "rejected"),
        "complete": ("accepted", "accepted"),
    }
    if any(
        tuple(
            report["matrix"][name]["contracts"][v]["status"]
            for v in ("initial", "revised")
        )
        != statuses
        for name, statuses in expected.items()
    ):
        raise RuntimeError("migration matrix did not establish the required outcomes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
