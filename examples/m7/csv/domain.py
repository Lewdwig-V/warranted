"""Prototype CSV domain: the M2 transformation evaluator behind the M7 checker shape."""

import json
import runpy
from pathlib import Path

from warranted import (
    DEFAULT_WORKER_IMAGE,
    CheckContext,
    LeanProof,
    ProofTarget,
    Verdict,
    VerdictStatus,
)

HERE = Path(__file__).resolve().parent
M2_SOURCE = HERE.parent.parent / "m2/experiments.py"
M2 = runpy.run_path(str(M2_SOURCE))


class Transformation:
    """Four independent obligations. Feedback names failed obligations only.

    The current offset and identifier definition are the task's single `offset-*`
    and `definition-*` files, so a revision changes them by replacing the file.
    """

    version = "2"
    isolated = True  # a pure function of the candidate and task bytes

    def check(self, ctx: CheckContext) -> Verdict:
        offset = _one(ctx.inputs, "offset-")
        reference = json.loads(ctx.private["references.json"])[offset]
        equality = json.loads(ctx.inputs[_one(ctx.inputs, "definition-")])["equality"]
        try:
            candidate = M2["decode"](ctx.candidate["result.json"])
            obligations = M2["evaluate"](
                M2["read_rows"](ctx.inputs["input.csv"]),
                candidate,
                reference,
                equality,
            )
        except KeyError:
            return Verdict(VerdictStatus.REJECTED, {"error": "result.json is missing"})
        except ValueError as error:
            return Verdict(VerdictStatus.REJECTED, {"error": str(error)})
        except RecursionError:
            return Verdict(
                VerdictStatus.REJECTED, {"error": "result.json is too deeply nested"}
            )
        status = (
            VerdictStatus.PASSED
            if all(obligations.values())
            else VerdictStatus.REJECTED
        )
        return Verdict(status, {"obligations": obligations}, {"offset": offset})


def _one(files, prefix: str) -> str:
    """The single versioned file with this prefix; the task must supply exactly one."""
    names = [name for name in files if name.startswith(prefix)]
    if len(names) != 1:
        raise ValueError(f"task needs exactly one {prefix}* file, found {names}")
    return names[0]


UNIQUENESS = ProofTarget(
    HERE / "UniquenessChallenge.lean", "Warranted.uniqueness_preserved"
)
TIMESTAMP = ProofTarget(
    HERE / "TimestampChallenge.lean", "Warranted.timestamp_roundtrip"
)


def _candidate_rows(ctx: CheckContext) -> list | None:
    try:
        rows = M2["decode"](ctx.candidate["result.json"])["rows"]
    except (KeyError, TypeError, ValueError, RecursionError):
        return None
    return rows if type(rows) is list else None


def uniqueness_premises(ctx: CheckContext) -> dict[str, bool]:
    """M4's applicability premise: the input IDs are unique."""
    source = [row[0] for row in M2["read_rows"](ctx.inputs["input.csv"])]
    return {"input_unique": len(source) == len(set(source))}


def uniqueness_correspondence(ctx: CheckContext) -> dict[str, bool]:
    """M4's model correspondence: the candidate is an identity selection of the IDs."""
    source = [row[0] for row in M2["read_rows"](ctx.inputs["input.csv"])]
    rows = _candidate_rows(ctx)
    well_formed = rows is not None and all(
        type(r) is list and len(r) == 3 and type(r[0]) is str for r in rows
    )
    remaining = iter(source)
    # In order, each candidate ID is a source ID: the mapping is the identity.
    return {"identity_selection": well_formed and all(r[0] in remaining for r in rows)}


def timestamp_correspondence(ctx: CheckContext) -> dict[str, bool]:
    """M5's timestamp application: one fixed offset, in whole seconds, for every row."""
    from datetime import datetime

    local = {r[0]: r[1] for r in M2["read_rows"](ctx.inputs["input.csv"])}
    offsets = set()
    for row in _candidate_rows(ctx) or []:
        try:
            utc = datetime.strptime(row[1], "%Y-%m-%dT%H:%M:%SZ")
            offsets.add((datetime.fromisoformat(local[row[0]]) - utc).total_seconds())
        except (KeyError, TypeError, ValueError, IndexError):
            return {"single_offset": False}
    return {"single_offset": len(offsets) == 1 and all(o == int(o) for o in offsets)}


class CsvDomain:
    name = "csv-transformation"
    version = "0.1"
    worker_image = DEFAULT_WORKER_IMAGE
    checkers = {
        "transformation": Transformation(),
        "uniqueness": LeanProof(
            UNIQUENESS,
            premises=uniqueness_premises,
            correspondence=uniqueness_correspondence,
        ),
        "timestamp": LeanProof(TIMESTAMP, correspondence=timestamp_correspondence),
    }
    sources = (M2_SOURCE,)  # loaded with runpy, so not found by following imports
