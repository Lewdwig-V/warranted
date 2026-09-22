"""Run the migration worker across an approved revision and a forced restart."""

import argparse
import json
import os
import runpy
import signal
from collections import Counter
from dataclasses import asdict
from functools import partial
from importlib.metadata import version
from pathlib import Path
from time import perf_counter_ns
from uuid import uuid4

os.environ["MSWEA_SILENT_STARTUP"] = "1"

from warranted import worker  # noqa: E402
from warranted.acceptance import (  # noqa: E402
    Acceptance,
    AcceptanceContext,
    Evidence,
    _encode,
)
from warranted.exports import export_evidence  # noqa: E402
from warranted.ledger import (  # noqa: E402
    Ledger,
    Manifest,
    Observation,
    Origin,
    Outcome,
    Request,
    Result,
    Snapshot,
)
from warranted.sandbox import SANDBOX_ID, Sandbox  # noqa: E402
from warranted.worker import (  # noqa: E402
    AttemptResult,
    Episode,
    record_once,
    run_workflow,
    submitted_candidate,
    unresolved,
)

HERE = Path(__file__).resolve().parent
M5 = runpy.run_path(str(HERE / "demo.py"))
MODEL = "m5-fixed-migration-v1"


def snapshots() -> dict[str, Snapshot]:
    captured = M5["snapshots"]()
    files = {
        name.removeprefix("seed/"): value.data.decode()
        for name, value in captured.items()
        if name.startswith("seed/")
    }
    approval = M5["decode"](captured["contracts.json"].data)
    captured["repository-files.json"] = Snapshot(
        _encode(files), "m5/worker-repository", "1"
    )
    captured["revision.txt"] = Snapshot(
        (
            approval["revision"]["reason"]
            + "\n"
            + approval["revision"]["requirement"]
            + "\nAll initial requirements remain binding.\n"
        ).encode(),
        "m5/approved-worker-revision",
        "1",
    )
    for name, path in (
        ("recovery.py", Path(__file__)),
        ("worker.py", Path(worker.__file__)),
    ):
        captured[name] = Snapshot(path.read_bytes(), "m5/host/" + name, "1")
    for phase, name in (("initial", "one-way"), ("revised", "complete")):
        source = captured[f"candidate-{name}.py"].data.decode()
        # The fixed model proposes source. Only the contained shell executes it.
        command = f"""python - <<'PY'
import json, subprocess, sys
from pathlib import Path
assert Path('revision.txt').exists() is {phase == "revised"!r}
files = json.loads(Path('repository-files.json').read_text())
Path('repo').mkdir(exist_ok=True)
for name, content in files.items():
    Path('repo', name).write_text(content)
Path('repo/migrate.py').write_text({source!r})
tested = subprocess.run([sys.executable, 'example_test.py'], cwd='repo',
                        capture_output=True, timeout=3)
sys.stderr.buffer.write(tested.stdout + tested.stderr)
if tested.returncode:
    raise SystemExit(tested.returncode)
candidate = {{'migrate.py': Path('repo/migrate.py').read_text()}}
Path('result.json').write_text(json.dumps(candidate))
print('COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT')
PY"""
        captured[f"model-{phase}.json"] = Snapshot(
            _encode({"command": command}), "m5/fixed-model/" + phase, "1"
        )
    return captured


def environment() -> dict[str, str]:
    return {
        **M5["environment"](),
        "model": MODEL,
        "policy": "m5-two-episodes-v1",
        **{
            name: version(name)
            for name in ("mini-swe-agent", "langgraph", "langgraph-checkpoint-sqlite")
        },
    }


def initialize(root: Path) -> None:
    root.mkdir(parents=True)
    with Ledger.create(
        root / "ledger",
        Manifest(
            "m5-worker-recovery",
            "1",
            str(uuid4()),
            str(uuid4()),
            environment(),
            {"model": 2, "tool": 2, "batch": 2, "check": 3},
        ),
        snapshots(),
    ):
        pass


def validate(ledger: Ledger) -> None:
    captured = snapshots()
    if (
        ledger.project.manifest.environment != environment()
        or captured.keys() != ledger.project.snapshots.keys()
    ):
        raise ValueError("fixture or environment changed")
    for name, value in captured.items():
        pinned = ledger.project.snapshots[name]
        if (pinned.origin, pinned.version, ledger.read_artifact(pinned.artifact)) != (
            value.origin,
            value.version,
            value.data,
        ):
            raise ValueError("fixture or host changed: " + name)


def ref(ledger: Ledger, name: str) -> Evidence:
    return Evidence(name, ledger.project.snapshots[name].artifact)


def witness(root: Path, request: Request) -> None:
    with (root / "worker-dispatches.jsonl").open("ab") as log:
        log.write(
            _encode(
                {"operation": request.origin.operation_id, "unit": request.origin.kind}
            )
            + b"\n"
        )


def model(root: Path, request: Request, payload: bytes) -> AttemptResult:
    started = perf_counter_ns()
    phase = request.origin.operation_id.split("/")[1]
    witness(root, request)
    with Ledger.open(root / "ledger") as ledger:
        response = ledger.read_artifact(ref(ledger, f"model-{phase}.json").artifact)
    return AttemptResult(
        Result(Outcome.SUCCEEDED, 0, {"model": 1}, perf_counter_ns() - started),
        {"response": response},
    )


def propose(root: Path, phase: str, previous: dict | None = None) -> Evidence:
    with Ledger.open(root / "ledger") as ledger:
        objective = ledger.read_artifact(
            ref(ledger, "seed/README.md").artifact
        ).decode()
        if phase == "revised":
            objective += (
                "\n"
                + ledger.read_artifact(ref(ledger, "revision.txt").artifact).decode()
            )
            target = Evidence.restored(previous["target"])
            objective += (
                "\nPrevious submitted source:\n"
                + ledger.read_artifact(target.artifact).decode()
            )
            objective += "\nCurrent independent checks:\n" + json.dumps(
                previous["checks"], sort_keys=True
            )
    objective += (
        "\nRead repository-files.json for the seed files. Submit result.json as "
        "one JSON object mapping only migrate.py to its complete source text."
    )
    episode = Episode(
        phase,
        objective,
        ("repository-files.json",) + (("revision.txt",) if phase == "revised" else ()),
        model=MODEL,
        environment=SANDBOX_ID,
        max_steps=1,
        continues="initial" if phase == "revised" else None,
    )

    def execute(request: Request, payload: bytes) -> AttemptResult:
        witness(root, request)
        with Sandbox(root / "ledger", episode) as isolated:
            return isolated(request, payload)

    result = run_workflow(
        root / "ledger",
        root / "graph.sqlite3",
        episode,
        model=partial(model, root),
        environment=execute,
    )
    if result.get("exit_status") != "Submitted":
        raise RuntimeError("worker did not submit a candidate")
    with Ledger.open(root / "ledger") as ledger:
        return submitted_candidate(ledger, phase)


def revision(ledger: Ledger) -> tuple[Observation, dict] | None:
    events = [o for o in ledger.history() if o.origin.operation_id == "m5/revision"]
    if not events:
        return None
    if len(events) != 1:
        raise ValueError("ambiguous revision")
    event = events[0]
    value = M5["decode"](ledger.read_artifact(event.artifacts["revision.json"]))
    approval = M5["decode"](
        ledger.read_artifact(ref(ledger, "contracts.json").artifact)
    )
    if value["approval"] != {"owner": approval["owner"], **approval["revision"]}:
        raise ValueError("revision lacks pinned owner approval")
    target = Evidence.restored(value["target"])
    inputs = {
        ref(ledger, "contracts.json").name: ref(ledger, "contracts.json").artifact,
        target.name: target.artifact,
    }
    for name in ("check", "decision"):
        op = next(
            op
            for op in ledger.operations()
            if op.request.origin.operation_id == value[name]
        )
        ledger.lookup(op.request)
        if not op.completion or op.completion.observation.sequence >= event.sequence:
            raise ValueError("revision preceded the first assessment")
        for channel, artifact in op.completion.observation.artifacts.items():
            ledger.read_artifact(artifact)
            inputs[Evidence.captured(op.completion.observation, channel).name] = (
                artifact
            )
    if event.origin != Origin("m5/revision", "revision", "m5-host", "1", inputs):
        raise ValueError("revision evidence changed")
    return event, value


def assess(
    ledger: Ledger, session: str, target: Evidence, old_receipt: str | None = None
) -> dict:
    event = revision(ledger)
    phase = "revised" if event else "initial"
    policy = ref(ledger, f"policy-{phase}.json")
    current = Evidence.captured(event[0], "revision.json") if event else policy
    fields = M5["decode"](ledger.read_artifact(policy.artifact))["requirements"]
    cases = M5["decode"](ledger.read_artifact(ref(ledger, "references.json").artifact))
    try:
        source = M5["candidate_source"](ledger.read_artifact(target.artifact))
    except (ValueError, RecursionError):
        source = None
    batch_req = M5["request"](
        ledger,
        "migration-batch",
        (
            "host.py",
            "recovery.py",
            "sandbox.py",
            "containers.py",
            *(f"case-{name}.json" for name in cases),
        ),
        {target.name: target.artifact},
    )

    def execute():
        if source is None:
            return AttemptResult(
                Result(Outcome.FAILED, 1, {"batch": 1}, 0),
                {"diagnostic": b"candidate may change only migrate.py"},
            )
        episode = Episode(
            "assess-" + target.artifact.digest[:24],
            "Assess captured source.",
            (),
            environment=SANDBOX_ID,
        )
        return M5["execute"](
            ledger.root.parent,
            episode,
            {name: case["input"] for name, case in cases.items()},
            source,
        )

    batch = M5["perform"](ledger, session, batch_req, "batch", execute)
    inputs = {
        target.name: target.artifact,
        current.name: current.artifact,
        **{
            Evidence.captured(batch.observation, channel).name: artifact
            for channel, artifact in batch.observation.artifacts.items()
        },
    }
    req = M5["request"](
        ledger,
        "migration-check",
        (
            policy.name,
            "contracts.json",
            "references.json",
            "repository.json",
            "host.py",
            "recovery.py",
        ),
        inputs,
    )
    context = AcceptanceContext(policy, current, {"evaluate": req})
    boundary = Acceptance(ledger, session, partial(M5["resolve"], target, context))
    stale = boundary.accept(target, {"evaluate": old_receipt}) if old_receipt else None
    checked = M5["perform"](
        ledger,
        session,
        req,
        "check",
        partial(M5["evaluate"], ledger, cases, batch, source is not None, fields),
    )
    decision = boundary.accept(target, {"evaluate": req.origin.operation_id})
    return {
        "target": asdict(target),
        "status": decision.status,
        "checks": M5["decode"](
            ledger.read_artifact(checked.observation.artifacts["result.json"])
        ),
        "check": req.origin.operation_id,
        "decision": decision.completion.observation.origin.operation_id,
        "old_receipt": stale.status if stale else None,
    }


def commit_revision(ledger: Ledger, session: str, assessed: dict) -> None:
    approval = M5["decode"](
        ledger.read_artifact(ref(ledger, "contracts.json").artifact)
    )
    target = Evidence.restored(assessed["target"])
    inputs = {
        "contracts.json": ref(ledger, "contracts.json").artifact,
        target.name: target.artifact,
    }
    for name in ("check", "decision"):
        op = next(
            op
            for op in ledger.operations()
            if op.request.origin.operation_id == assessed[name]
        )
        for channel, artifact in op.completion.observation.artifacts.items():
            inputs[Evidence.captured(op.completion.observation, channel).name] = (
                artifact
            )
    record_once(
        ledger,
        session,
        Origin("m5/revision", "revision", "m5-host", "1", inputs),
        {
            "revision.json": _encode(
                {
                    "approval": {"owner": approval["owner"], **approval["revision"]},
                    **{key: assessed[key] for key in ("target", "check", "decision")},
                }
            )
        },
    )


def demonstrate(stage: str, root: Path, *, crash: bool = False) -> dict:
    with Ledger.open(root / "ledger") as ledger:
        validate(ledger)
        unresolved(ledger)
        before = len(ledger.operations())
        event = revision(ledger)
        if (stage == "start" and event) or (stage == "resume" and not event):
            raise ValueError(
                "start requires no revision; resume requires a committed revision"
            )
        report = {"stage": stage}
        if event:
            old = assess(
                ledger,
                ledger.start_session(),
                Evidence.restored(event[1]["target"]),
                event[1]["check"],
            )
            report.update(old_candidate=old, old_receipt=old["old_receipt"])
    target = propose(
        root, "initial" if stage == "start" else "revised", report.get("old_candidate")
    )
    with Ledger.open(root / "ledger") as ledger:
        session = ledger.start_session()
        assessed = assess(ledger, session, target)
        report["candidate"] = assessed
        if stage == "start":
            commit_revision(ledger, session, assessed)
        balances = ledger.accounting()
        dispatches = Counter()
        for filename in ("worker-dispatches.jsonl", "executions.jsonl"):
            path = root / filename
            if path.exists():
                dispatches.update(
                    json.loads(line)["unit"] for line in path.read_bytes().splitlines()
                )
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
        report.update(
            session=session,
            project_id=ledger.project.project_id,
            environment=environment(),
            export=str(export),
            spent={key: value.spent for key, value in balances.items()},
            reserved={key: value.reserved for key, value in balances.items()},
            dispatches=dict(dispatches),
            new_operations=len(ledger.operations()) - before,
            operation_elapsed_ns=sum(
                op.completion.result.elapsed_ns
                for op in ledger.operations()
                if op.completion
            ),
        )
        (root / "reports").mkdir(exist_ok=True)
        for filename in (stage, session):
            (root / "reports" / (filename + ".json")).write_bytes(_encode(report))
        if crash:
            # All containers are stopped. Kill with the authoritative ledger open.
            os.kill(os.getpid(), signal.SIGKILL)
        return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("start", "resume"))
    parser.add_argument("root", type=Path)
    parser.add_argument(
        "--crash",
        action="store_true",
        help="kill the host after committing the revision",
    )
    args = parser.parse_args()
    if args.crash and args.stage != "start":
        parser.error("--crash requires start")
    root = args.root.resolve()
    if args.stage == "start" and not (root / "ledger").exists():
        initialize(root)
    report = demonstrate(args.stage, root, crash=args.crash)
    print(json.dumps(report, indent=2))
    return 0 if report["candidate"]["status"] == "accepted" else 1


if __name__ == "__main__":
    raise SystemExit(main())
