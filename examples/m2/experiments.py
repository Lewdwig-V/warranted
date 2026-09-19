"""Three fixed M2 experiments using the trusted local acceptance boundary."""

import argparse
import csv
import hashlib
import io
import json
import platform
from collections import Counter
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from time import perf_counter_ns
from uuid import uuid4

from warranted import acceptance
from warranted.acceptance import Acceptance, AcceptanceContext, Evidence
from warranted.exports import export_evidence
from warranted.ledger import (
    ArtifactRef,
    Ledger,
    Manifest,
    Observation,
    Operation,
    Origin,
    Outcome,
    Request,
    Result,
    Snapshot,
    SnapshotRef,
)

FILES = (
    "input.csv",
    "contract.md",
    "versions.json",
    "references.json",
    "candidates.json",
    "acceptance.json",
)
SCENARIOS = ("matrix", "unchanged", "annotation", "offset", "definition")


def encode(value) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()


def decode(data: bytes):
    def unique_keys(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    return json.loads(data, object_pairs_hook=unique_keys)


def read_rows(data: bytes) -> list[list]:
    rows = list(csv.reader(io.StringIO(data.decode()), strict=True))
    if not rows or rows[0] != ["id", "timestamp", "value"]:
        raise ValueError("expected id,timestamp,value CSV header")
    result = []
    for identifier, timestamp, value in rows[1:]:
        datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%S")
        if not identifier or not identifier.isascii():
            raise ValueError("expected a nonempty ASCII identifier")
        result.append([identifier, timestamp, int(value)])
    return result


def unique_ids(identifiers: list[str], equality: str) -> bool:
    if equality == "ascii-case-insensitive":
        identifiers = [identifier.lower() for identifier in identifiers]
    elif equality != "exact":
        raise ValueError("unsupported identifier equality")
    return len(identifiers) == len(set(identifiers))


def evaluate(
    source: list[list], candidate: dict, reference: dict, equality: str
) -> dict:
    """Four independent obligations; malformed candidates never establish success."""
    if type(candidate) is not dict or set(candidate) != {"rows", "totals"}:
        raise ValueError("expected candidate rows and totals")
    rows, totals = candidate["rows"], candidate["totals"]
    if type(rows) is not list or any(
        type(row) is not list
        or len(row) != 3
        or type(row[0]) is not str
        or not row[0]
        or not row[0].isascii()
        or type(row[1]) is not str
        or type(row[2]) is not int
        for row in rows
    ):
        raise ValueError("expected rows with an ASCII ID, timestamp, and integer value")
    if type(totals) is not dict or any(
        type(key) is not str or type(value) is not int for key, value in totals.items()
    ):
        raise ValueError("expected integer daily totals")
    expected_times = {row[0]: row[1] for row in reference["rows"]}
    # Deliberately independent of the transformation's aggregation routine.
    observed_totals = {}
    valid_dates = True
    for _, timestamp, value in rows:
        try:
            date = datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%SZ").date().isoformat()
        except ValueError:
            valid_dates = False
            continue
        observed_totals[date] = observed_totals.get(date, 0) + value
    return {
        "unique_ids": unique_ids([row[0] for row in rows], equality),
        "preserved_rows": [(row[0], row[2]) for row in rows]
        == [(row[0], row[2]) for row in source],
        "utc_timestamps": all(expected_times.get(row[0]) == row[1] for row in rows),
        "daily_totals": valid_dates
        and totals == observed_totals == reference["totals"],
    }


@dataclass(frozen=True)
class Interpretation:
    offset: str = "offset-v1"
    definition: str = "definition-v1"
    annotation: str = "annotation-v1"


def snapshots(fixture: Path) -> dict[str, Snapshot]:
    captured = {
        name: Snapshot(
            (fixture / name).read_bytes(),
            f"m2/{name}",
            "2" if name == "contract.md" else "1",
        )
        for name in FILES
    }
    captured["host.py"] = Snapshot(
        Path(__file__).read_bytes(), "m2/experiments.py", "2"
    )
    captured["acceptance.py"] = Snapshot(
        Path(acceptance.__file__).read_bytes(), "warranted/acceptance.py", "1"
    )
    for name, value in decode(captured["versions.json"].data).items():
        captured[name] = Snapshot(
            encode(value), f"m2/versions.json#{name}", value["version"]
        )
    for file, prefix in (
        ("references.json", "reference"),
        ("candidates.json", "candidate"),
    ):
        for name, value in decode(captured[file].data).items():
            captured[f"{prefix}/{name}"] = Snapshot(
                encode(value), f"m2/{file}#{name}", "1"
            )
    captured["witness"] = Snapshot(encode(["rA", "ra"]), "m2/contract.md#witness", "1")
    return captured


def environment(scenario: str) -> dict[str, str]:
    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "policy": "fixed-m2-script-v2",
        "evaluator": "four-obligations-v1",
        "model": "none",
        "split": "development",
        "scenario": scenario,
    }


class Experiment:
    """Fixture plumbing using the existing ledger for evidence and dispatch."""

    def __init__(self, ledger: Ledger, session: str, root: Path):
        self.ledger, self.session, self.root = ledger, session, root

    def ref(self, name: str) -> Evidence:
        return Evidence(name, self.ledger.project.snapshots[name].artifact)

    def read(self, ref: Evidence):
        return decode(self.ledger.read_artifact(ref.artifact))

    def origins(self, inputs: dict[str, Evidence]) -> dict[str, ArtifactRef]:
        return {ref.name: ref.artifact for ref in inputs.values()}

    def request(self, kind: str, inputs: dict[str, Evidence]) -> Request:
        identity = hashlib.sha256(
            encode({key: asdict(ref) for key, ref in inputs.items()})
        ).hexdigest()
        return Request(
            Origin(f"{kind}/{identity}", kind, "m2-fixture", "1", self.origins(inputs)),
            self.ledger.project,
        )

    def perform(self, kind: str, inputs: dict[str, Evidence], compute) -> Operation:
        req = self.request(kind, inputs)
        operation = self.ledger.reserve(self.session, req, {"synthetic-work": 1})
        if operation.completion is None:
            if not self.ledger.begin(self.session, req):
                raise RuntimeError("operation outcome is unknown; no retry")
            started = perf_counter_ns()
            with (self.root / "executions.jsonl").open("ab") as counter:
                counter.write(
                    encode(req.origin.operation_id).replace(b"\n", b"") + b"\n"
                )
            # An unexpected host exception leaves an unknown operation reserved.
            value = compute()
            self.ledger.complete(
                self.session,
                req,
                Result(
                    Outcome.SUCCEEDED,
                    0,
                    {"synthetic-work": 1},
                    perf_counter_ns() - started,
                ),
                {"result.json": encode(value)},
            )
            operation = self.ledger.lookup(req)
        if operation.completion.result.outcome is not Outcome.SUCCEEDED:
            raise RuntimeError("checker did not complete successfully")
        return operation

    def result_ref(self, operation: Operation) -> Evidence:
        return Evidence.captured(operation.completion.observation, "result.json")

    def result(self, operation: Operation):
        return self.read(self.result_ref(operation))

    def record(self, kind: str, inputs: dict[str, Evidence], value: dict):
        if kind == "candidate":
            # Reuse the exact submission when its producing operations are unchanged.
            # ponytail: scan this small history; index submissions if it grows.
            for item in self.ledger.history():
                if item.origin.kind == kind and item.origin.inputs == self.origins(
                    inputs
                ):
                    if self.ledger.read_artifact(
                        item.artifacts["candidate.json"]
                    ) != encode(value):
                        raise ValueError(
                            "candidate changed under the same producing operations"
                        )
                    return item
        return self.ledger.record(
            self.session,
            Origin(str(uuid4()), kind, "m2-fixture", "1", self.origins(inputs)),
            {f"{kind}.json": encode(value)},
        )

    def interpretation(self) -> tuple[Interpretation, Observation]:
        events = [
            item for item in self.ledger.history() if item.origin.kind == "revision"
        ]
        if len(events) != 1:
            raise RuntimeError("missing committed revision checkpoint")
        event = events[0]
        return Interpretation(
            **self.read(Evidence.captured(event, "revision.json"))["current"]
        ), event

    def pipeline(self, current: Interpretation) -> tuple[Evidence, list[str]]:
        before = {op.request.origin.operation_id for op in self.ledger.operations()}
        facts = self.perform(
            "source-facts",
            {"source": self.ref("input.csv")},
            lambda: read_rows(
                self.ledger.read_artifact(self.ref("input.csv").artifact)
            ),
        )
        offset = self.ref(current.offset)

        def normalize():
            zone = timezone(timedelta(minutes=self.read(offset)["minutes"]))
            return [
                [
                    identifier,
                    datetime.fromisoformat(timestamp)
                    .replace(tzinfo=zone)
                    .astimezone(UTC)
                    .strftime("%Y-%m-%dT%H:%M:%SZ"),
                    value,
                ]
                for identifier, timestamp, value in self.result(facts)
            ]

        normalized = self.perform(
            "normalize", {"facts": self.result_ref(facts), "offset": offset}, normalize
        )

        def aggregate():
            totals = Counter()
            for _, timestamp, value in self.result(normalized):
                totals[timestamp[:10]] += value
            return dict(totals)

        totals = self.perform(
            "aggregate",
            {"normalized": self.result_ref(normalized), "offset": offset},
            aggregate,
        )
        candidate = self.record(
            "candidate",
            {
                "normalized": self.result_ref(normalized),
                "totals": self.result_ref(totals),
            },
            {"rows": self.result(normalized), "totals": self.result(totals)},
        )
        reused = [
            op.request.origin.kind
            for op in (facts, normalized, totals)
            if op.request.origin.operation_id in before
        ]
        return Evidence.captured(candidate, "candidate.json"), reused

    def check_inputs(
        self, candidate: Evidence, current: Interpretation
    ) -> dict[str, Evidence]:
        return {
            "candidate": candidate,
            "source": self.ref("input.csv"),
            "offset": self.ref(current.offset),
            "definition": self.ref(current.definition),
            "reference": self.ref(f"reference/{current.offset}"),
        }

    def check(self, candidate: Evidence, current: Interpretation) -> Operation:
        inputs = self.check_inputs(candidate, current)
        return self.perform(
            "evaluate",
            inputs,
            lambda: evaluate(
                read_rows(self.ledger.read_artifact(inputs["source"].artifact)),
                self.read(candidate),
                self.read(inputs["reference"]),
                self.read(inputs["definition"])["equality"],
            ),
        )

    def witness(self, current: Interpretation) -> Operation:
        definition = self.ref(current.definition)
        return self.perform(
            "witness",
            {"witness": self.ref("witness"), "definition": definition},
            lambda: unique_ids(
                self.read(self.ref("witness")), self.read(definition)["equality"]
            ),
        )

    def source_style(self) -> Operation:
        return self.perform(
            "source-style",
            {"source": self.ref("input.csv")},
            lambda: {
                "explicit_offsets": all(
                    datetime.fromisoformat(row[1]).tzinfo is not None
                    for row in read_rows(
                        self.ledger.read_artifact(self.ref("input.csv").artifact)
                    )
                )
            },
        )

    def acceptance_context(self, candidate: Evidence) -> AcceptanceContext:
        current, event = self.interpretation()
        return AcceptanceContext(
            self.ref("acceptance.json"),
            Evidence.captured(event, "revision.json"),
            {
                "evaluate": self.request(
                    "evaluate", self.check_inputs(candidate, current)
                ),
                "source-style": self.request(
                    "source-style", {"source": self.ref("input.csv")}
                ),
            },
        )

    def decide(self, candidate: Evidence, receipt_id: str | None) -> str:
        boundary = Acceptance(self.ledger, self.session, self.acceptance_context)
        exception = boundary.record_exception(
            candidate,
            "explicit_source_offsets",
            "The fixture owner supplies a separately versioned fixed offset.",
        )
        return boundary.accept(
            candidate,
            {
                "evaluate": receipt_id,
                "source-style": self.request(
                    "source-style", {"source": self.ref("input.csv")}
                ).origin.operation_id,
            },
            {"explicit_source_offsets": exception},
        ).status


def run_scenario(
    command: str, root: Path, scenario: str, captured: dict[str, Snapshot]
) -> dict:
    if command == "start":
        root.mkdir(parents=True)
        ledger = Ledger.create(
            root / "ledger",
            Manifest(
                "m2-data-transformation",
                "2",
                str(uuid4()),
                str(uuid4()),
                environment(scenario),
                {"synthetic-work": 20},
            ),
            captured,
        )
    else:
        ledger = Ledger.open(root / "ledger")
    with ledger:
        # Bind current fixture and host bytes before a new session or cached reuse.
        refs = {
            name: SnapshotRef(
                ArtifactRef(hashlib.sha256(s.data).hexdigest(), len(s.data)),
                s.origin,
                s.version,
            )
            for name, s in captured.items()
        }
        context = replace(
            ledger.project,
            snapshots=refs,
            manifest=replace(
                ledger.project.manifest, environment=environment(scenario)
            ),
        )
        ledger.lookup(
            Request(Origin("context-check", "context", "m2-fixture", "1", {}), context)
        )
        if any(op.completion is None for op in ledger.operations()):
            raise RuntimeError(
                "pending or unknown operation; reservation retained, no retry"
            )
        before = sum(bool(op.reservation) for op in ledger.operations())
        host = Experiment(ledger, ledger.start_session(), root)
        initial = Interpretation()
        report = {
            "session_id": host.session,
            "project_id": ledger.project.project_id,
            "fixture_version": ledger.project.manifest.fixture_version,
            "environment": dict(ledger.project.manifest.environment),
        }
        if command == "start":
            host.source_style()
            if scenario == "matrix":
                candidates = {
                    name: host.ref(f"candidate/{name}")
                    for name in decode(captured["candidates.json"].data)
                }
            else:
                candidate, _ = host.pipeline(initial)
                candidates = {"main": candidate}
                report["candidate_digest"] = candidate.artifact.digest
            receipts = {
                name: host.check(ref, initial).request.origin.operation_id
                for name, ref in candidates.items()
            }
            if scenario == "definition":
                report["witness_before"] = host.result(host.witness(initial))
            current = replace(
                initial,
                **(
                    {scenario: f"{scenario}-v2"}
                    if scenario in ("annotation", "offset", "definition")
                    else {}
                ),
            )
            host.record(
                "revision",
                candidates,
                {
                    "checkpoint": "first-submission-before-final-acceptance",
                    "owner": "fixture-owner",
                    "previous": asdict(initial),
                    "current": asdict(current),
                    "receipts": receipts,
                    "candidates": {
                        name: asdict(ref) for name, ref in candidates.items()
                    },
                    "reason": {
                        "matrix": "Retain independent failures across restart.",
                        "unchanged": "Restart without revision.",
                        "annotation": "Clarify the source annotation only.",
                        "offset": "Correct the documented fixed offset to +00:00.",
                        "definition": "Ignore ASCII letter case; preserve ID spelling.",
                    }[scenario],
                },
            )
        else:
            current, event = host.interpretation()
            revision = host.read(Evidence.captured(event, "revision.json"))
            receipts = revision["receipts"]
            candidates = {
                name: Evidence.restored(ref)
                for name, ref in revision["candidates"].items()
            }
            if scenario == "matrix":
                empty = candidates["empty"]
                narrow = host.perform(
                    "narrow-unique",
                    {"candidate": empty, "definition": host.ref(current.definition)},
                    lambda: unique_ids(
                        [row[0] for row in host.read(empty)["rows"]], "exact"
                    ),
                )
                report["narrow_empty_pass"] = host.result(narrow)
                report["decisions"] = {
                    name: host.decide(ref, receipts[name])
                    for name, ref in candidates.items()
                }
            else:
                old_candidate = candidates["main"]
                report["before_reassessment"] = host.decide(
                    old_candidate, receipts["main"]
                )
                candidate, reused = host.pipeline(current)
                report.update(
                    candidate=host.read(candidate),
                    candidate_digest=candidate.artifact.digest,
                    reused_pipeline=reused,
                )
                if scenario == "offset":
                    old_check = host.check(old_candidate, current)
                    report["old_candidate_decision"] = host.decide(
                        old_candidate, old_check.request.origin.operation_id
                    )
                check = host.check(candidate, current)
                report["decision"] = host.decide(
                    candidate, check.request.origin.operation_id
                )
                if scenario == "definition":
                    report["witness_before"] = host.result(host.witness(initial))
                    report["witness_after"] = host.result(host.witness(current))
        balance = ledger.accounting()["synthetic-work"]
        report.update(
            new_operations=sum(bool(op.reservation) for op in ledger.operations())
            - before,
            decision_operations=sum(
                op.request.origin.kind == "decision" for op in ledger.operations()
            ),
            rule_exceptions=sum(
                op.request.origin.kind == "rule-exception" for op in ledger.operations()
            ),
            limit=balance.limit,
            spent=balance.spent,
            reserved=balance.reserved,
            operation_elapsed_ns=sum(
                op.completion.result.elapsed_ns for op in ledger.operations()
            ),
            execution_count=len((root / "executions.jsonl").read_text().splitlines()),
        )
        exports = root / "exports"
        exports.mkdir(exist_ok=True)
        report["export"] = str(
            export_evidence(
                ledger,
                exports / host.session,
                snapshots=tuple(
                    name
                    for name in ledger.project.snapshots
                    if name not in ("host.py", "acceptance.py")
                ),
                observations={
                    item.sequence: tuple(item.artifacts) for item in ledger.history()
                },
                operations=tuple(
                    op.request.origin.operation_id for op in ledger.operations()
                ),
            )
        )
        reports = root / "reports"
        reports.mkdir(exist_ok=True)
        (reports / f"{host.session}.json").write_bytes(encode(report))
        return report


def experiments(command: str, root: Path, fixture: Path) -> dict:
    captured = snapshots(fixture)
    return {
        scenario: run_scenario(command, root / scenario, scenario, captured)
        for scenario in SCENARIOS
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("start", "resume"))
    parser.add_argument("root", type=Path)
    parser.add_argument(
        "--fixture", type=Path, default=Path(__file__).parent / "fixture"
    )
    args = parser.parse_args()
    reports = experiments(args.command, args.root, args.fixture)
    print(json.dumps(reports, indent=2))
    passed = all(
        report["spent"] == report["execution_count"] and report["reserved"] == 0
        for report in reports.values()
    )
    if args.command == "resume":
        matrix = reports["matrix"]
        passed = (
            passed
            and matrix["narrow_empty_pass"]
            and all(
                status == ("accepted" if name == "correct" else "rejected")
                for name, status in matrix["decisions"].items()
            )
            and all(reports[name]["decision"] == "accepted" for name in SCENARIOS[1:])
        )
        passed = (
            passed
            and reports["offset"]["old_candidate_decision"] == "rejected"
            and reports["definition"]["witness_before"]
            and not reports["definition"]["witness_after"]
        )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
