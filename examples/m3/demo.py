"""A fixed serial worker baseline reusing M2's private checker and revision rules."""

import argparse
import json
import os
import platform
import runpy
import select
import signal
import subprocess
import sys
from contextlib import contextmanager
from dataclasses import asdict
from importlib.metadata import version
from pathlib import Path
from time import perf_counter_ns
from uuid import uuid4

os.environ["MSWEA_SILENT_STARTUP"] = "1"

from warranted import attempts, sandbox, worker  # noqa: E402
from warranted.acceptance import Evidence  # noqa: E402
from warranted.attempts import ReceiptService  # noqa: E402
from warranted.exports import export_evidence  # noqa: E402
from warranted.ledger import Ledger, Manifest, Snapshot  # noqa: E402
from warranted.sandbox import SANDBOX_ID, Sandbox  # noqa: E402
from warranted.worker import Episode, run_workflow  # noqa: E402

HERE = Path(__file__).resolve().parent
M2 = runpy.run_path(str(HERE.parent / "m2/experiments.py"))
Interpretation, Experiment = M2["Interpretation"], M2["Experiment"]
MODEL = "m3-scripted-transform-v1"


def snapshots():
    result = M2["snapshots"](HERE.parent / "m2/fixture")
    for name, path in {
        "m3-host.py": Path(__file__),
        "fake-service.py": HERE / "fake_service.py",
        "worker-task.md": HERE / "worker-task.md",
        "model-command.sh": HERE / "model-command.sh",
        "worker.py": Path(worker.__file__),
        "sandbox.py": Path(sandbox.__file__),
        "attempts.py": Path(attempts.__file__),
    }.items():
        result[name] = Snapshot(path.read_bytes(), f"m3/{name}", "1")
    result["service-config"] = Snapshot(
        M2["encode"](
            {
                "service_id": MODEL,
                "mode": "receipts",
                "drop_response": False,
                "responses": [
                    json.dumps({"command": (HERE / "model-command.sh").read_text()})
                ],
                "usage": [1],
            }
        ),
        "m3/fake-model-configuration",
        "1",
    )
    return result


def environment():
    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "podman": subprocess.check_output(["podman", "--version"], text=True).strip(),
        "sandbox": SANDBOX_ID,
        "model": MODEL,
        "policy": "fixed-two-episodes-v1",
        "evaluator": "four-obligations-v1",
        "split": "development",
        "scenario": "offset",
        **{
            name: version(name)
            for name in ("mini-swe-agent", "langgraph", "langgraph-checkpoint-sqlite")
        },
    }


def initialize(root: Path):
    root.mkdir(parents=True)
    captured = snapshots()
    with Ledger.create(
        root / "ledger",
        Manifest(
            "m3-changed-offset",
            "1",
            str(uuid4()),
            str(uuid4()),
            environment(),
            {"model": 4, "tool": 4, "synthetic-work": 20},
        ),
        captured,
    ):
        pass
    (root / "service").mkdir()
    (root / "service/config.json").write_bytes(captured["service-config"].data)


def validate(root: Path):
    captured = snapshots()
    with Ledger.open(root / "ledger") as ledger:
        if (
            set(captured) != set(ledger.project.snapshots)
            or ledger.project.manifest.environment != environment()
        ):
            raise ValueError("fixture or environment changed")
        for name, value in captured.items():
            ref = ledger.project.snapshots[name]
            if (ref.origin, ref.version, ledger.read_artifact(ref.artifact)) != (
                value.origin,
                value.version,
                value.data,
            ):
                raise ValueError("fixture or host bytes changed")
    if (root / "service/config.json").read_bytes() != captured["service-config"].data:
        raise ValueError("model configuration changed")


@contextmanager
def model_service(root: Path):
    with subprocess.Popen(
        [sys.executable, str(HERE / "fake_service.py"), str(root / "service")],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ) as process:
        try:
            ready, _, _ = select.select([process.stdout], [], [], 10)
            if not ready:
                raise RuntimeError("fake service did not start")
            yield ReceiptService(process.stdout.readline().decode().strip(), MODEL)
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def candidate(root: Path, current, client: ReceiptService) -> Evidence:
    episode = Episode(
        "initial" if current.offset == "offset-v1" else "revised",
        "Read worker-task.md and the current offset. Write result.json, then submit.",
        ("input.csv", current.offset, "worker-task.md"),
        model=MODEL,
        environment=SANDBOX_ID,
        max_steps=2,
        continues=None if current.offset == "offset-v1" else "initial",
    )
    with Sandbox(root / "ledger", episode) as container:

        def execute(request, payload):
            # Independent dispatch witness, not proof that an unknown shell ran.
            with (root / "tool-dispatches.jsonl").open("a") as log:
                log.write(json.dumps(request.origin.operation_id) + "\n")
            return container(request, payload)

        result = run_workflow(
            root / "ledger",
            root / "graph.sqlite3",
            episode,
            model=client,
            environment=execute,
            reconcile=client.reconcile,
        )
    if result.get("exit_status") != "Submitted":
        raise RuntimeError("worker did not submit a candidate")
    with Ledger.open(root / "ledger") as ledger:
        matches = [
            op
            for op in ledger.operations()
            if op.request.origin.operation_id.startswith(
                f"episode/{episode.episode_id}/tool/"
            )
            and op.completion is not None
            and "candidate/result.json" in op.completion.observation.artifacts
        ]
        if len(matches) != 1:
            raise RuntimeError("submission lacks one exact captured candidate")
        operation = ledger.lookup(matches[0].request)
        return Evidence.captured(
            operation.completion.observation, "candidate/result.json"
        )


def claims(host, target, current):
    host.claim_refs["candidate"] = host.claims.record(
        "The worker submitted a transformation under these inputs.",
        target,
        {"source": host.ref("input.csv"), "offset": host.ref(current.offset)},
        complete=True,
    )
    host.claim_checks("main", target, current)


def count(path):
    return len(path.read_bytes().splitlines()) if path.exists() else 0


def demonstrate(stage: str, root: Path, client: ReceiptService) -> dict:
    started = perf_counter_ns()
    validate(root)
    initial = Interpretation()
    with Ledger.open(root / "ledger") as ledger:
        previous = {op.request.origin.operation_id for op in ledger.operations()}
        host = Experiment(ledger, ledger.start_session(), root)
        if stage == "start":
            if any(item.origin.kind == "revision" for item in ledger.history()):
                raise ValueError("revision already committed; use resume")
            current = initial
        else:
            current, event = host.interpretation()
            revision = host.read(Evidence.captured(event, "revision.json"))
    target = candidate(root, current, client)
    with Ledger.open(root / "ledger") as ledger:
        host = Experiment(ledger, ledger.start_session(), root)
        report = {
            "stage": stage,
            "session": host.session,
            "project_id": ledger.project.project_id,
            "fixture_id": ledger.project.manifest.fixture_id,
            "fixture_version": ledger.project.manifest.fixture_version,
            "environment": dict(ledger.project.manifest.environment),
        }
        host.source_style()
        if stage == "resume":
            old = Evidence.restored(revision["candidates"]["main"])
            report["support_before"] = host.support(
                {
                    name: Evidence.restored(ref)
                    for name, ref in revision["claims"].items()
                },
                current,
            )
            report["old_receipt"] = host.decide(old, revision["receipts"]["main"])
            old_check = host.check(old, current)
            report["old_candidate"] = host.decide(
                old, old_check.request.origin.operation_id
            )
            report["old_checks"] = host.result(old_check)
        check = host.check(target, current)
        claims(host, target, current)
        report.update(
            checks=host.result(check),
            decision=host.decide(target, check.request.origin.operation_id),
            candidate=asdict(target),
            support_after=host.support(host.claim_refs, current),
        )
        if stage == "start":
            host.revise(
                {"main": target},
                {"main": check.request.origin.operation_id},
                Interpretation(offset="offset-v2"),
            )
        balances = ledger.accounting()
        report.update(
            spent={unit: balance.spent for unit, balance in balances.items()},
            reserved={unit: balance.reserved for unit, balance in balances.items()},
            limits={unit: balance.limit for unit, balance in balances.items()},
            model_requests=count(root / "service/requests.jsonl"),
            tool_dispatches=count(root / "tool-dispatches.jsonl"),
            checker_executions=count(root / "executions.jsonl"),
            operation_elapsed_ns=sum(
                op.completion.result.elapsed_ns
                for op in ledger.operations()
                if op.completion
            ),
            new_operation_elapsed_ns=sum(
                op.completion.result.elapsed_ns
                for op in ledger.operations()
                if op.completion and op.request.origin.operation_id not in previous
            ),
            host_elapsed_ns=perf_counter_ns() - started,
        )
        (root / "exports").mkdir(exist_ok=True)
        report["export"] = str(
            export_evidence(
                ledger,
                root / "exports" / host.session,
                snapshots=("input.csv", "worker-task.md", current.offset),
                observations={
                    o.sequence: tuple(o.artifacts)
                    for o in ledger.history()
                    if o.origin.kind
                    in (
                        "evaluate",
                        "source-style",
                        "decision",
                        "claim",
                        "rule-exception",
                    )
                    or "candidate/result.json" in o.artifacts
                },
                operations=tuple(
                    op.request.origin.operation_id for op in ledger.operations()
                ),
            )
        )
        (root / "reports").mkdir(exist_ok=True)
        encoded = M2["encode"](report)
        (root / "reports" / f"{host.session}.json").write_bytes(encoded)
        (root / "reports" / f"{stage}.json").write_bytes(encoded)
        return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("start", "resume"))
    parser.add_argument("root", type=Path)
    parser.add_argument(
        "--crash",
        action="store_true",
        help="kill this host after the durable report and resource cleanup",
    )
    args = parser.parse_args()
    root = args.root.resolve()
    if args.stage == "start" and not (root / "ledger").exists():
        initialize(root)
    validate(root)
    with model_service(root) as client:
        report = demonstrate(args.stage, root, client)
    if args.crash:
        os.kill(os.getpid(), signal.SIGKILL)
    print(json.dumps(report, indent=2))
    return 0 if report["decision"] == "accepted" else 1


if __name__ == "__main__":
    raise SystemExit(main())
