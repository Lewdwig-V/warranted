"""Development controls for reaching submission; not an A–E comparison."""

import argparse
import base64
import json
import runpy
from dataclasses import replace
from pathlib import Path
from time import monotonic_ns
from unittest.mock import patch
from uuid import uuid4

from warranted import sandbox, worker
from warranted.acceptance import Evidence, _encode
from warranted.containers import _run
from warranted.contexts import Condition
from warranted.ledger import (
    Ledger,
    Manifest,
    Origin,
    Outcome,
    Request,
    Result,
    Snapshot,
)
from warranted.worker import AttemptResult, Journal, record_once, submitted_candidate

HERE = Path(__file__).resolve().parent
T = runpy.run_path(str(HERE / "treatments.py"))
R = T["R"]
SUBMIT = b"""import json, sys
from pathlib import Path
root = Path(__file__).resolve().parent
source = Path(sys.argv[1]) if len(sys.argv) == 2 else root / 'workspace/migrate.py'
(root / 'result.json').write_text(json.dumps({'migrate.py': source.read_text()}))
print('COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT')
"""
GUIDE = b"""Complete the migration described in task.md. Keep your source in
/work/workspace/migrate.py. The submission payload is a JSON object mapping the
single filename migrate.py to its complete Python source text. It is not the
configuration produced by that program.
When ready, use a separate final command: python /work/submit.py
The helper packages your source into /work/result.json and prints the marker.
It does not run tests or establish acceptance. The host checks the same independent
requirements after submission. Do not put test output before the submission command.
"""


class ProgressModel(worker.WorkerModel):
    def query(self, messages, **kwargs):
        remaining = self.journal.episode.max_steps - self.index
        return super().query(
            messages
            + [
                {
                    "role": "user",
                    "content": (
                        f"Model turns remaining, including this one: {remaining}. "
                        "Each response must contain one shell command. Format errors "
                        "also consume a turn. To submit your source, run "
                        "python /work/submit.py as a separate final command."
                    ),
                }
            ],
            **kwargs,
        )


def snapshots(bundle, client, runtime=None):
    return {
        **T["snapshots"](
            "migration", bundle, model_client=client, model_runtime=runtime
        ),
        "diagnostics.py": Snapshot(Path(__file__).read_bytes(), "m5/diagnostics", "1"),
        "submit.py": Snapshot(SUBMIT, "m5/submission-helper", "1"),
        "submission.md": Snapshot(GUIDE, "m5/submission-guide", "1"),
    }


def initialize(root, bundle, control, client, max_steps=12, *, runtime=None):
    if control not in {"submission", "plain"} or not 1 <= max_steps <= 100:
        raise ValueError("unknown control or invalid turn limit")
    root.mkdir(parents=True)
    with Ledger.create(
        root / "ledger",
        Manifest(
            "m5-submission-control",
            "1",
            str(uuid4()),
            str(uuid4()),
            {
                **T["environment"]("migration", Condition.A, client, max_steps),
                "control": control,
                "scope": "development-diagnostic",
            },
            {**T["CAPS"], "model": max_steps, "tool": max_steps, "capture": 1},
        ),
        snapshots(bundle, client, runtime),
    ):
        pass


class DiagnosticSandbox(sandbox.Sandbox):
    def __exit__(self, *exc):
        try:
            # Normal submission has already captured files and removed the container.
            if _run(["container", "exists", self.name]).returncode == 0:
                with Ledger.open(self.root) as ledger:
                    journal = Journal(ledger, self.episode)
                    req = Request(
                        Origin(
                            "diagnostic/unfinished",
                            "capture",
                            "m5-diagnostic",
                            "1",
                            journal.completion_inputs(),
                        ),
                        ledger.project,
                    )

                    def capture():
                        started = monotonic_ns()
                        result = _run(
                            [
                                "exec",
                                "--user=0:0",
                                self.name,
                                "python",
                                "-I",
                                "-c",
                                sandbox._CAPTURE,
                                "unfinished",
                            ],
                            output_limit=4 * sandbox.CANDIDATE_LIMIT,
                        )
                        raw = {"stdout": result.stdout, "stderr": result.stderr}
                        if result.returncode == 0:
                            raw.update(
                                {
                                    "unfinished/" + name: base64.b64decode(
                                        data, validate=True
                                    )
                                    for name, data in json.loads(result.stdout).items()
                                }
                            )
                        return AttemptResult(
                            Result(
                                Outcome.SUCCEEDED
                                if result.returncode == 0
                                else Outcome.FAILED,
                                result.returncode,
                                {"capture": 1},
                                monotonic_ns() - started,
                            ),
                            raw,
                        )

                    R["M5"]["perform"](ledger, journal.session, req, "capture", capture)
        finally:
            super().__exit__(*exc)


def run(root, bundle, client):
    started = monotonic_ns()
    with Ledger.open(root / "ledger") as ledger:
        env = ledger.project.manifest.environment
        control = env["control"]
        expected_env = {
            **T["environment"]("migration", Condition.A, client, int(env["max_steps"])),
            "control": control,
            "scope": "development-diagnostic",
        }
        runtime = (
            ledger.read_artifact(ledger.project.snapshots["runtime"].artifact)
            if client
            else None
        )
        current = snapshots(bundle, client, runtime)
        if env != expected_env or current.keys() != ledger.project.snapshots.keys():
            raise ValueError("diagnostic configuration changed")
        for name, value in current.items():
            ref = ledger.project.snapshots[name]
            if (ref.origin, ref.version, ledger.read_artifact(ref.artifact)) != (
                value.origin,
                value.version,
                value.data,
            ):
                raise ValueError("diagnostic source changed: " + name)
        worker.unresolved(ledger)
        host = T["M2"]["Experiment"](ledger, ledger.start_session(), root)
        episode = T["prepare"](
            host, "migration", Condition.A, "initial", None, {}, client
        )
        if control == "submission":
            episode = replace(
                episode,
                objective=(
                    "This is a submission-only control. The task is already solved in "
                    "prepared-result.json. Do not edit or solve it again. Copy it "
                    "to /work/result.json and submit by printing "
                    "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT as the first stdout line "
                    "of a successful command."
                ),
                files={
                    **episode.files,
                    "prepared-result.json": host.ref("candidate-one-way.json"),
                },
            )
        else:
            episode = replace(
                episode,
                objective=(
                    f"Use at most {episode.max_steps} model turns, each containing one "
                    "shell command. Read task.md, context.json, submission.md, "
                    "repository-files.json, and the other task inputs "
                    "together. Implement and test the task in workspace/migrate.py. "
                    "Use python /work/submit.py as a separate final command to package "
                    "and submit source. submission.md defines the completion protocol."
                ),
                files={
                    **episode.files,
                    "submission.md": host.ref("submission.md"),
                    "submit.py": host.ref("submit.py"),
                },
            )
    error = None
    try:
        with (
            patch.dict(T["propose"].__globals__, {"Sandbox": DiagnosticSandbox}),
            patch.object(
                worker,
                "WorkerModel",
                ProgressModel if control == "plain" else worker.WorkerModel,
            ),
        ):
            T["propose"](root, episode, client)
    except RuntimeError as failure:
        error = str(failure)
    with Ledger.open(root / "ledger") as ledger:
        session = ledger.start_session()
        submitted, assessed, unfinished = False, None, {}
        try:
            target = submitted_candidate(ledger, "initial")
        except ValueError:
            target = None
        if target is not None:
            submitted = True
            assessed = R["assess"](ledger, session, target)
        else:
            captures = [
                o
                for o in ledger.operations()
                if o.request.origin.operation_id == "diagnostic/unfinished"
                and o.completion
            ]
            if captures:
                raw = captures[0].completion.observation
                if "unfinished/result.json" in raw.artifacts:
                    unfinished["payload"] = R["assess"](
                        ledger,
                        session,
                        Evidence.captured(raw, "unfinished/result.json"),
                    )
                if "unfinished/workspace.json" in raw.artifacts:
                    files = sandbox.decode_workspace(
                        ledger.read_artifact(raw.artifacts["unfinished/workspace.json"])
                    )
                    source = None
                    if "migrate.py" in files:
                        try:
                            source = base64.b64decode(files["migrate.py"]).decode()
                        except UnicodeDecodeError:
                            unfinished["source"] = {
                                "status": "unsupported",
                                "reason": "source is not UTF-8",
                            }
                    if source is not None:
                        event = record_once(
                            ledger,
                            session,
                            Origin(
                                "diagnostic/package-unsubmitted-source",
                                "diagnostic",
                                "m5-diagnostic",
                                "1",
                                {
                                    Evidence.captured(
                                        raw, "unfinished/workspace.json"
                                    ).name: raw.artifacts["unfinished/workspace.json"]
                                },
                            ),
                            {"payload.json": _encode({"migrate.py": source})},
                        )
                        unfinished["source"] = R["assess"](
                            ledger, session, Evidence.captured(event, "payload.json")
                        )
        result = {
            "control": control,
            "submitted": submitted,
            "assessment": assessed,
            "unfinished_assessment": unfinished,
            "error": error,
            "elapsed_ns": monotonic_ns() - started,
            "spent": {k: v.spent for k, v in ledger.accounting().items()},
            "reserved": {k: v.reserved for k, v in ledger.accounting().items()},
        }
        (root / "report.json").write_bytes(_encode(result))
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("init", "run"))
    parser.add_argument("root", type=Path)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--control", choices=("submission", "plain"), required=True)
    T["add_model_arguments"](parser)
    args = parser.parse_args()
    client = T["client_from_arguments"](args)
    if args.action == "init":
        initialize(args.root, args.bundle, args.control, client, args.max_steps)
    else:
        with Ledger.open(args.root / "ledger") as ledger:
            if ledger.project.manifest.environment[
                "control"
            ] != args.control or ledger.project.manifest.environment[
                "max_steps"
            ] != str(args.max_steps):
                raise ValueError("diagnostic control or budget changed")
        print(_encode(run(args.root, args.bundle, client)).decode())


if __name__ == "__main__":
    main()
