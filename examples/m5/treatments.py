"""Run fixed, local, or OpenRouter A–E workers across an approved revision."""

import argparse
import base64
import hashlib
import json
import os
import runpy
import signal
from contextlib import ExitStack
from dataclasses import asdict, replace
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

os.environ["MSWEA_SILENT_STARTUP"] = "1"

from warranted import (  # noqa: E402
    containers,
    contexts,
    sandbox,
    worker,
)
from warranted import proofs as verifier
from warranted.acceptance import Evidence, _encode  # noqa: E402
from warranted.chat_completions import LocalChatCompletions  # noqa: E402
from warranted.containers import PODMAN_COMMAND_TIMEOUT_SECONDS  # noqa: E402
from warranted.contexts import Condition, capture_context, capture_history  # noqa: E402
from warranted.exports import export_evidence  # noqa: E402
from warranted.ledger import (  # noqa: E402
    Ledger,
    Manifest,
    Origin,
    Outcome,
    Result,
    Snapshot,
)
from warranted.openrouter import OpenRouterChatCompletions, command_format  # noqa: E402
from warranted.proof_receipts import Proofs, proof_status  # noqa: E402
from warranted.sandbox import SANDBOX_ID, Sandbox, decode_workspace  # noqa: E402
from warranted.worker import (  # noqa: E402
    AttemptResult,
    Episode,
    record_once,
    run_workflow,
    submitted_candidate,
    submitted_files,
    unresolved,
)

HERE = Path(__file__).resolve().parent
LOCAL = runpy.run_path(str(HERE / "local_model.py"))
P = runpy.run_path(str(HERE / "proof_cases.py"))
R = runpy.run_path(str(HERE / "recovery.py"))
CSV = runpy.run_path(str(HERE.parent / "m3/demo.py"))
M2, M5 = P["M2"], R["M5"]
CAPS = {"model": 8, "tool": 8, "proof": 6, "synthetic-work": 24, "batch": 4, "check": 5}


class WorkerChat(LocalChatCompletions):
    @property
    def parameters(self):
        return {
            **super().parameters,
            "response_format": command_format(),
        }


def local_client(model: str | None, *, base_url: str, max_tokens: int, timeout: int):
    return WorkerChat(base_url, model, max_tokens, timeout) if model else None


def capture_runtime(client):
    if isinstance(client, OpenRouterChatCompletions):
        return client.runtime()
    return LOCAL["runtime"](client)


def add_model_arguments(parser):
    models = parser.add_mutually_exclusive_group()
    models.add_argument("--local-model")
    models.add_argument("--openrouter-model")
    parser.add_argument("--api-key-file", type=Path)
    parser.add_argument("--base-url", default="http://127.0.0.1:11434/v1")
    parser.add_argument("--max-tokens", type=int, default=1536)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--max-steps", type=int, default=4)


def client_from_arguments(args):
    if args.openrouter_model:
        if args.base_url != "http://127.0.0.1:11434/v1":
            raise ValueError("OpenRouter uses its fixed HTTPS endpoint")
        return OpenRouterChatCompletions(
            "https://openrouter.ai/api/v1",
            args.openrouter_model,
            args.max_tokens,
            args.timeout,
            key_file=args.api_key_file,
        )
    if args.api_key_file:
        raise ValueError("--api-key-file requires --openrouter-model")
    return local_client(
        args.local_model,
        base_url=args.base_url,
        max_tokens=args.max_tokens,
        timeout=args.timeout,
    )


def snapshots(
    family: str, bundle: Path, *, model_client=None, model_runtime: bytes | None = None
) -> dict[str, Snapshot]:
    captured = P["snapshots"](bundle)
    if family == "csv":
        captured = {
            name: value
            for name, value in captured.items()
            if not name.startswith(("migration/", "model/migration/"))
            and name not in {"proof/migration", "target/migration"}
        }
    else:
        captured = {
            name: captured[name]
            for name in (
                "proof/migration",
                "target/migration",
                "model/migration/complete",
                "model/migration/drop-label",
                "proofs.py",
                "proof_receipts.py",
                "proof-bundle.json",
                "proof-cases.py",
                "proof-intent.json",
            )
        }
    captured.update((CSV if family == "csv" else R)["snapshots"]())
    captured.update(LOCAL["implementation"]())
    for name, path in {
        "treatments.py": Path(__file__),
        "recovery.py": Path(R["__file__"]),
        "m2-host.py": Path(M2["__file__"]),
        "m4-host.py": Path(P["M4"]["__file__"]),
        "m5-host.py": Path(M5["__file__"]),
        "contexts.py": Path(contexts.__file__),
        "containers.py": Path(containers.__file__),
        "worker.py": Path(worker.__file__),
        "sandbox.py": Path(sandbox.__file__),
    }.items():
        captured[name] = Snapshot(path.read_bytes(), "m5/host/" + name, "1")
    for phase in ("initial", "revised"):
        if family == "csv":
            program = (
                captured["model-command.sh"]
                .data.decode()
                .split("'PY'\n", 1)[1]
                .split("\nPY\n", 1)[0]
            )
            minutes = 60 if phase == "initial" else 0
            model = program.replace(
                "json.loads(offsets[0].read_text())['minutes']", str(minutes)
            )
            regression = (
                "import csv, json\nfrom pathlib import Path\n"
                "r = json.loads(Path('result.json').read_text())\n"
                "source = list(csv.DictReader(Path('input.csv').open()))\n"
                "assert [v[0] for v in r['rows']] == [v['id'] for v in source]\n"
                "assert len({v[0] for v in r['rows']}) == len(source)\n"
            )
            notes = "Using offset " + str(minutes)
            command = (
                "python - <<'PY'\nfrom pathlib import Path\n"
                + f"Path('workspace/program.py').write_text({program!r})\n"
                + "notes = Path('workspace/notes.md')\n"
                + "notes.write_text((notes.read_text() if notes.exists() else '') "
                + f"+ {notes + chr(10)!r})\n"
                + "PY\npython workspace/program.py || exit 1\n"
                + "if [ -f regression.py ]; then python regression.py || exit 1; fi\n"
                + "printf 'COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT\\n'\n"
            )
        else:
            name = "one-way" if phase == "initial" else "complete"
            model = captured[f"candidate-{name}.py"].data.decode()
            regression = captured["seed/example_test.py"].data.decode()
            if phase == "revised":
                regression = regression.replace(
                    "self.assertEqual(\n",
                    "again = subprocess.run([sys.executable, '-I', 'migrate.py'], "
                    "input=result.stdout, capture_output=True, timeout=2, check=True)\n"
                    "        self.assertEqual(json.loads(again.stdout), "
                    "json.loads(result.stdout))\n        self.assertEqual(\n",
                )
            command = json.loads(captured[f"model-{phase}.json"].data)["command"]
            command = command.replace("'repo'", "'workspace/repo'").replace(
                "'repo/", "'workspace/repo/"
            )
            command = command.replace(
                "candidate = ",
                "if Path('regression.py').exists():\n"
                "    subprocess.run([sys.executable, "
                "str(Path('regression.py').resolve())], "
                "cwd='workspace/repo', timeout=5, check=True)\n"
                "notes = Path('workspace/notes.md')\n"
                "notes.write_text((notes.read_text() if notes.exists() else '') "
                f"+ {'Migration ' + phase + chr(10)!r})\n"
                "candidate = ",
            )
        captured[f"model-{phase}.json"] = Snapshot(
            _encode({"command": command}), "m5/treatment-worker/" + phase, "1"
        )
        captured[f"executable-{phase}.py"] = Snapshot(
            model.encode(), "m5/public-model/" + phase, "1"
        )
        captured[f"regression-{phase}.py"] = Snapshot(
            regression.encode(), "m5/public-regression/" + phase, "1"
        )
    captured["tools.md"] = Snapshot(
        b"Use the shell and files. Save source and notes under workspace/; "
        b"regular files there survive submission and restart. Submit result.json "
        b"at the work root. Optional Lean verification: save a complete source "
        b"file as workspace/uniqueness.lean, workspace/timestamp.lean, or "
        b"workspace/migration.lean. The host checks each supplied source once "
        b"at submission, charges one proof unit, and records the result. Formal "
        b"target definitions are in tool-targets.json. These tools and limits "
        b"are the same in A-E.\n",
        "m5/tool-contract",
        "1",
    )
    if model_client:
        captured["model-api"] = model_client.snapshot
        captured["runtime"] = Snapshot(
            model_runtime
            if model_runtime is not None
            else capture_runtime(model_client),
            model_client.provider,
            "1",
        )
    return captured


def environment(
    family: str, condition: Condition, model_client=None, max_steps=4
) -> dict:
    result = {
        **(CSV if family == "csv" else R)["environment"](),
        "family": family,
        "condition": str(condition),
        "max_steps": str(max_steps),
        "policy": "m5-live-treatments-v1" if model_client else "m5-fixed-treatments-v1",
        "model": model_client.model if model_client else "m5-fixed-two-proposals-v1",
    }
    if model_client:
        result["model_service"] = model_client.service_id
        result["model_provider"] = model_client.provider
    return result


def initialize(
    root: Path,
    family: str,
    condition: Condition,
    bundle: Path,
    *,
    model_client=None,
    model_runtime: bytes | None = None,
    max_steps: int = 4,
) -> None:
    if type(max_steps) is not int or not 1 <= max_steps <= 100:
        raise ValueError("max_steps must be between 1 and 100")
    root.mkdir(parents=True)
    with Ledger.create(
        root / "ledger",
        Manifest(
            "m5-treatments-" + family,
            "1",
            str(uuid4()),
            str(uuid4()),
            environment(family, condition, model_client, max_steps),
            {**CAPS, "model": 2 * max_steps, "tool": 2 * max_steps},
        ),
        snapshots(
            family,
            bundle,
            model_client=model_client,
            model_runtime=model_runtime,
        ),
    ):
        pass


def validate(
    ledger: Ledger, bundle: Path, *, model_client=None
) -> tuple[str, Condition]:
    env = ledger.project.manifest.environment
    family, condition = env["family"], Condition(env["condition"])
    if env != environment(family, condition, model_client, int(env["max_steps"])):
        raise ValueError("fixture or environment changed")
    runtime = (
        ledger.read_artifact(ledger.project.snapshots["runtime"].artifact)
        if model_client
        else None
    )
    captured = snapshots(
        family, bundle, model_client=model_client, model_runtime=runtime
    )
    if captured.keys() != ledger.project.snapshots.keys():
        raise ValueError("fixture or environment changed")
    for name, value in captured.items():
        pinned = ledger.project.snapshots[name]
        if (pinned.origin, pinned.version, ledger.read_artifact(pinned.artifact)) != (
            value.origin,
            value.version,
            value.data,
        ):
            raise ValueError("fixture or host changed: " + name)
    return family, condition


def record(host, name: str, refs: dict, files: dict[str, bytes]):
    return record_once(
        host.ledger,
        host.session,
        Origin("treatment/" + name, "treatment", "m5-host", "1", host.origins(refs)),
        files,
    )


def checkpoint(host, family):
    if family == "migration":
        return R["revision"](host.ledger)
    if any(o.origin.kind == "revision" for o in host.ledger.history()):
        current, event = host.interpretation()
        return event, host.read(Evidence.captured(event, "revision.json"))
    return None


def assess(host, family, target, old_receipt=None):
    if family == "migration":
        return R["assess"](host.ledger, host.session, target, old_receipt)
    current = (
        host.interpretation()[0] if checkpoint(host, family) else M2["Interpretation"]()
    )
    host.source_style()
    stale = host.decide(target, old_receipt) if old_receipt else None
    checked = host.check(target, current)
    return {
        "target": asdict(target),
        "status": host.decide(target, checked.request.origin.operation_id),
        "checks": host.result(checked),
        "check": checked.request.origin.operation_id,
        "old_receipt": stale,
    }


def prepare(host, family, condition, phase, old, proofs, model_client=None):
    revised = phase == "revised"
    if revised and checkpoint(host, family) is None:
        raise ValueError("revised context requires a committed revision")
    if family == "csv":
        dependency = host.ref("offset-v2" if revised else "offset-v1")
        ordinary = {
            "input.csv": host.ref("input.csv"),
            dependency.name: dependency,
            "task.md": host.ref("worker-task.md"),
        }
    else:
        dependency = host.ref("revision.txt" if revised else "seed/README.md")
        ordinary = {
            "repository-files.json": host.ref("repository-files.json"),
            "task.md": host.ref("seed/README.md"),
        }
        if revised:
            ordinary["revision.txt"] = dependency
    ordinary["tools.md"] = host.ref("tools.md")
    # Tool definitions are equally available. The migration target is released
    # only after its current-version requirement has been approved.
    targets = (
        ("uniqueness", "timestamp")
        if family == "csv"
        else (("migration",) if revised else ())
    )
    target_refs = {name: host.ref("target/" + name) for name in targets}
    target_event = record(
        host,
        phase + "/tools",
        target_refs,
        {
            "tool-targets.json": _encode(
                {
                    name: host.ledger.read_artifact(ref.artifact).decode()
                    for name, ref in target_refs.items()
                }
            )
        },
    )
    ordinary["tool-targets.json"] = Evidence.captured(target_event, "tool-targets.json")
    workspace = None
    if revised:
        candidate = submitted_candidate(host.ledger, "initial")
        ordinary["previous-result.json"] = candidate
        workspace = submitted_files(host.ledger, "initial")["workspace.json"]
        feedback = record(
            host,
            "revised/feedback",
            {
                "candidate": candidate,
                "revision": Evidence.captured(
                    checkpoint(host, family)[0], "revision.json"
                ),
            },
            {
                "feedback.json": _encode(
                    {
                        "status": old["status"],
                        "checks": old["checks"],
                        "old_receipt": old["old_receipt"],
                    }
                )
            },
        )
        ordinary["feedback.json"] = Evidence.captured(feedback, "feedback.json")
        results = next(
            o
            for o in host.ledger.history()
            if o.origin.operation_id == "treatment/initial/optional-proofs"
        )
        ordinary["proof-results.json"] = Evidence.captured(results, "results.json")
    layers = {level: {} for level in Condition}
    layers[Condition.A] = ordinary
    if condition >= Condition.B:
        exchanges = capture_history(
            host.ledger, host.session, phase, ("initial",) if revised else ()
        )
        prior = (
            next(
                (
                    o
                    for o in host.ledger.history()
                    if o.origin.operation_id == "worker-context/initial"
                ),
                None,
            )
            if revised
            else None
        )
        refs = {"exchanges": exchanges, **ordinary}
        rows = host.ledger.read_artifact(exchanges.artifact)
        if prior:
            disclosed = {
                name: Evidence.captured(prior, name)
                for name in prior.artifacts
                if name != "selection.json"
            }
            refs.update({"prior/" + name: ref for name, ref in disclosed.items()})
            rows = (
                _encode(
                    {
                        "kind": "disclosure",
                        "episode": "initial",
                        "files_base64": {
                            name: base64.b64encode(
                                host.ledger.read_artifact(ref.artifact)
                            ).decode()
                            for name, ref in disclosed.items()
                        },
                    }
                )
                + b"\n"
                + rows
            )
        rows += (
            _encode(
                {
                    "kind": "disclosure",
                    "episode": phase,
                    "files_base64": {
                        name: base64.b64encode(
                            host.ledger.read_artifact(ref.artifact)
                        ).decode()
                        for name, ref in ordinary.items()
                    },
                }
            )
            + b"\n"
        )
        event = record(host, phase + "/history", refs, {"history.jsonl": rows})
        layers[Condition.B] = {
            "history.jsonl": Evidence.captured(event, "history.jsonl")
        }
    if condition >= Condition.C:
        layers[Condition.C] = {
            "model.py": host.ref(f"executable-{phase}.py"),
            "regression.py": host.ref(f"regression-{phase}.py"),
        }
    if condition >= Condition.D:
        initial = host.ref("offset-v1" if family == "csv" else "seed/README.md")
        old_claim = host.claims.record(
            "Executable model under the initial input.",
            host.ref("executable-initial.py"),
            {"input": initial},
            complete=True,
        )
        new_claim = host.claims.record(
            "Executable model under the current input.",
            host.ref(f"executable-{phase}.py"),
            {"input": dependency},
            complete=True,
        )
        status = {}
        for name, ref in {"initial": old_claim, "current": new_claim}.items():
            assessed = host.claims.assess(ref, {"input": dependency})
            status[name] = {
                "validation": assessed.validation,
                "applicability": assessed.applicability,
                "dependencies": dict(assessed.dependencies),
            }
        event = record(
            host,
            phase + "/dependencies",
            {"initial": old_claim, "current": new_claim, "input": dependency},
            {"dependencies.json": _encode(status)},
        )
        layers[Condition.D] = {
            "dependencies.json": Evidence.captured(event, "dependencies.json")
        }
    if condition == Condition.E:
        refs = {
            name: Evidence.restored(value["receipt"]) for name, value in proofs.items()
        }
        event = record(
            host, phase + "/proofs", refs, {"proof-work.json": _encode(proofs)}
        )
        layers[Condition.E] = {
            "proof-work.json": Evidence.captured(event, "proof-work.json")
        }
        for name in targets:
            layers[Condition.E][name + ".lean"] = host.ref(
                "Solution.lean" if name == "uniqueness" else "proof/" + name
            )
    files = capture_context(host.ledger, host.session, phase, layers)
    max_steps = int(host.ledger.project.manifest.environment["max_steps"])
    container_timeout = 120
    if model_client:
        # Creation/load, capture, and cleanup, plus five calls per action:
        # info, exists, inspect, exec, and status.
        podman_calls = 7 + 5 * max_steps
        metadata_seconds = 3 * LOCAL["METADATA_REQUEST_TIMEOUT_SECONDS"]
        container_timeout = min(
            3600,
            max_steps * (model_client.timeout_seconds + metadata_seconds)
            + podman_calls * PODMAN_COMMAND_TIMEOUT_SECONDS
            + 60,
        )
    count = {4: "four", 12: "twelve"}.get(max_steps, str(max_steps))
    return Episode(
        phase,
        f"Use at most {count} shell commands. In the first command, inspect task.md, "
        "context.json, tools.md, repository-files.json if present, and the other "
        "task inputs together. Create and test result.json, then submit it. The "
        "final command must start with `printf '%s\\n' "
        "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT;` so the marker is the first "
        "stdout line. On continuation, use the restored workspace and current "
        "feedback.",
        (),
        model=model_client.model if model_client else "m5-fixed-two-proposals-v1",
        model_service=model_client.service_id if model_client else None,
        environment=SANDBOX_ID,
        max_steps=max_steps,
        container_timeout_seconds=container_timeout,
        continues="initial" if revised else None,
        files=files,
        workspace=workspace,
    )


def propose(root: Path, episode: Episode, model_client=None):
    def model(request, payload):
        if model_client:
            with Ledger.open(root / "ledger") as ledger:
                expected = ledger.read_artifact(
                    ledger.project.snapshots["runtime"].artifact
                )
            client = model_client
            preflight = {}
            if isinstance(client, OpenRouterChatCompletions):
                client = replace(client, key_sha256=json.loads(expected)["key_sha256"])
                failure = client.runtime_failure(expected, preflight)
            else:
                failure = LOCAL["runtime_failure"](client, expected)
            if failure is not None:
                return failure
            R["witness"](root, request)
            attempt = client(request, payload)
            return AttemptResult(attempt.result, {**preflight, **attempt.raw})
        R["witness"](root, request)
        with Ledger.open(root / "ledger") as ledger:
            data = ledger.read_artifact(
                ledger.project.snapshots[f"model-{episode.episode_id}.json"].artifact
            )
        return AttemptResult(
            Result(Outcome.SUCCEEDED, 0, {"model": 1}, 0), {"response": data}
        )

    isolated = None
    with ExitStack() as sandbox_scope:

        def execute(request, payload):
            nonlocal isolated
            R["witness"](root, request)
            if isolated is None:
                isolated = sandbox_scope.enter_context(
                    Sandbox(root / "ledger", episode)
                )
            return isolated(request, payload)

        result = run_workflow(
            root / "ledger",
            root / "graph.sqlite3",
            episode,
            model=model,
            environment=execute,
        )
    if result.get("exit_status") != "Submitted":
        raise RuntimeError("worker did not submit")


def optional_proofs(host, phase, bundle, family):
    workspace = submitted_files(host.ledger, phase)["workspace.json"]
    files = decode_workspace(host.ledger.read_artifact(workspace.artifact))
    allowed = (
        ("uniqueness", "timestamp")
        if family == "csv"
        else (("migration",) if checkpoint(host, family) else ())
    )
    results, receipts = {}, {"workspace": workspace}
    for name in allowed:
        if name + ".lean" not in files:
            continue
        source = record(
            host,
            phase + "/proposal/" + name,
            {"workspace": workspace},
            {"source.lean": base64.b64decode(files[name + ".lean"], validate=True)},
        )
        verifier = Proofs(host.ledger, host.session, bundle, target_id=name)
        request = verifier.check(
            "treatment/proof/" + phase + "/" + name,
            Evidence.captured(source, "source.lean"),
        )
        unresolved(host.ledger)
        operation = host.ledger.lookup(request)
        receipt = Evidence.captured(
            operation.completion.observation, "verification.json"
        )
        receipts[name] = receipt
        results[name] = {
            "status": proof_status(host.ledger, request, verifier.target),
            "receipt": asdict(receipt),
        }
    record(
        host, phase + "/optional-proofs", receipts, {"results.json": _encode(results)}
    )
    return results


def assurance(host, family, target, theorems):
    if family == "csv":
        source = host.ref("input.csv")
        app = P["M4"]["application"](
            host, "correct", target, source, theorems["uniqueness"]
        )
        unique = P["M4"]["support"](host, app, theorems["uniqueness"], source)
        model = host.ref("offset-v2")
        timestamp = P["application"](
            host,
            "timestamp",
            theorems["timestamp"],
            target,
            {"candidate": target, "source": source, "model": model},
            lambda: P["timestamp_correspondence"](
                host.ledger.read_artifact(source.artifact),
                host.read(target),
                host.read(model)["minutes"],
            ),
        )
        return {"uniqueness": unique, "timestamp": timestamp}
    batches = [
        op.completion
        for op in host.ledger.operations()
        if op.request.origin.kind == "migration-batch"
        and op.request.origin.inputs.get(target.name) == target.artifact
        and op.completion
    ]
    if len(batches) != 1:
        raise ValueError("assurance needs the exact candidate execution")
    batch = batches[0]
    raw = {
        name: host.ledger.read_artifact(ref)
        for name, ref in batch.observation.artifacts.items()
    }
    cases, model = host.ref("references.json"), host.ref("model/migration/complete")
    inputs = {
        "candidate": target,
        "cases": cases,
        "model": model,
        **{name: Evidence.captured(batch.observation, name) for name in raw},
    }
    return {
        "migration": P["application"](
            host,
            "migration",
            theorems["migration"],
            target,
            inputs,
            lambda: P["migration_correspondence"](
                host.read(cases), raw, host.read(model)
            ),
        )
    }


def demonstrate(
    stage: str, root: Path, bundle: Path, *, crash=False, model_client=None
):
    execute = verifier._verify

    def witnessed(data, captured, *, target_id, seconds):
        with (root / "proof-executions.jsonl").open("ab") as log:
            log.write(
                _encode(
                    {"target": target_id, "source": hashlib.sha256(data).hexdigest()}
                )
                + b"\n"
            )
        return execute(data, captured, target_id=target_id, seconds=seconds)

    with patch.object(verifier, "_verify", witnessed):
        return run_stage(stage, root, bundle, crash=crash, model_client=model_client)


def run_stage(stage: str, root: Path, bundle: Path, *, crash=False, model_client=None):
    phase = "initial" if stage == "start" else "revised"
    with Ledger.open(root / "ledger") as ledger:
        unresolved(ledger)
        family, condition = validate(ledger, bundle, model_client=model_client)
        before = len(ledger.operations())
        host = M2["Experiment"](ledger, ledger.start_session(), root)
        revision = checkpoint(host, family)
        if (stage == "start" and revision) or (stage == "resume" and not revision):
            raise ValueError(
                "start requires no revision; resume requires a committed revision"
            )
        matrix = {}
        if stage == "resume":
            events = [
                o
                for o in ledger.history()
                if o.origin.operation_id == "treatment/checkpoint"
            ]
            if len(events) != 1 or (
                events[0].origin.kind,
                events[0].origin.producer,
                events[0].origin.producer_version,
            ) != ("treatment", "m5-host", "1"):
                raise ValueError("resume requires a committed treatment checkpoint")
            matrix = host.read(Evidence.captured(events[0], "matrix.json"))
        targets = ("uniqueness", "timestamp") if family == "csv" else ("migration",)
        theorems, proofs = ({}, {})
        if condition == Condition.E and stage == "resume":
            theorems, proofs = P["proof_work"](host, bundle, targets)
        old = (
            assess(
                host,
                family,
                submitted_candidate(ledger, "initial"),
                revision[1]["check"]
                if family == "migration"
                else revision[1]["receipts"]["main"],
            )
            if revision
            else None
        )
        episode = prepare(host, family, condition, phase, old, proofs, model_client)
    propose(root, episode, model_client)
    with Ledger.open(root / "ledger") as ledger:
        host = M2["Experiment"](ledger, ledger.start_session(), root)
        target = submitted_candidate(ledger, phase)
        candidate = assess(host, family, target)
        spontaneous = optional_proofs(host, phase, bundle, family)
        if stage == "start":
            if condition == Condition.E and family == "csv":
                theorems, proofs = P["proof_work"](host, bundle, targets)
                matrix = P["csv_cases"](host, theorems)
            if family == "migration":
                R["commit_revision"](ledger, host.session, candidate)
                if condition == Condition.E:
                    theorems, proofs = P["proof_work"](host, bundle, targets)
                    matrix = {
                        "migration": P["migration_cases"](
                            host, theorems["migration"], prefix=""
                        )
                    }
            else:
                host.revise(
                    {"main": target},
                    {"main": candidate["check"]},
                    M2["Interpretation"](offset="offset-v2"),
                )
            record(
                host,
                "checkpoint",
                {"candidate": target, **theorems},
                {"matrix.json": _encode(matrix)},
            )
        support = (
            assurance(host, family, target, theorems)
            if stage == "resume" and condition == Condition.E
            else {}
        )
        qualified = candidate["status"] == "accepted" and (
            condition != Condition.E
            or bool(support)
            and all(value["status"] == "supported" for value in support.values())
        )
        result = {
            "stage": stage,
            "family": family,
            "condition": condition,
            "candidate": candidate,
            "old_candidate": old,
            "proofs": proofs,
            "spontaneous_proofs": spontaneous,
            "support": support,
            "qualified": qualified,
            "matrix": matrix,
        }
        record(
            host,
            "result/" + stage,
            {"candidate": target},
            {"result.json": _encode(result)},
        )
        balances = ledger.accounting()
        report = {
            **result,
            "environment": dict(ledger.project.manifest.environment),
            "spent": {key: value.spent for key, value in balances.items()},
            "reserved": {key: value.reserved for key, value in balances.items()},
            "limits": dict(ledger.project.manifest.allowances),
            "new_operations": len(ledger.operations()) - before,
            "elapsed_ns_by_kind": {
                kind: sum(
                    op.completion.result.elapsed_ns
                    for op in ledger.operations()
                    if op.completion and op.request.origin.kind == kind
                )
                for kind in {op.request.origin.kind for op in ledger.operations()}
            },
            "proof_bundle_build_elapsed_ns": json.loads(bundle.read_bytes())[
                "build_elapsed_ns"
            ],
            "proof_executions": P["M4"]["count"](root / "proof-executions.jsonl"),
        }
        (root / "exports").mkdir(exist_ok=True)
        report["export"] = str(
            export_evidence(
                ledger,
                root / "exports" / host.session,
                snapshots=tuple(ledger.project.snapshots),
                observations={o.sequence: tuple(o.artifacts) for o in ledger.history()},
                operations=tuple(
                    op.request.origin.operation_id for op in ledger.operations()
                ),
            )
        )
        (root / "reports").mkdir(exist_ok=True)
        (root / "reports" / (stage + ".json")).write_bytes(_encode(report))
        if crash:
            os.kill(os.getpid(), signal.SIGKILL)
        return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("start", "resume"))
    parser.add_argument("root", type=Path)
    parser.add_argument("--family", choices=("csv", "migration"), required=True)
    parser.add_argument(
        "--condition", type=Condition, choices=list(Condition), required=True
    )
    parser.add_argument("--bundle", type=Path, required=True)
    add_model_arguments(parser)
    parser.add_argument("--crash", action="store_true")
    args = parser.parse_args()
    if args.crash and args.stage != "start":
        parser.error("--crash requires start")
    root, bundle = args.root.resolve(), args.bundle.resolve()
    model_client = client_from_arguments(args)
    if args.stage == "start" and not (root / "ledger").exists():
        initialize(
            root,
            args.family,
            args.condition,
            bundle,
            model_client=model_client,
            max_steps=args.max_steps,
        )
    with Ledger.open(root / "ledger") as ledger:
        if str(args.max_steps) != ledger.project.manifest.environment["max_steps"]:
            raise ValueError("command limit changed")
        if (args.family, str(args.condition)) != (
            ledger.project.manifest.environment["family"],
            ledger.project.manifest.environment["condition"],
        ):
            raise ValueError("family or condition changed")
    report = demonstrate(
        args.stage, root, bundle, crash=args.crash, model_client=model_client
    )
    print(json.dumps(report, indent=2))
    return (
        0
        if report["candidate"]["status"] == "accepted"
        and (args.stage == "start" or report["qualified"])
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
