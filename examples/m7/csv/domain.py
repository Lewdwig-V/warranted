"""Prototype CSV domain: the M2 transformation evaluator behind the M7 checker shape."""

import json
import runpy
from pathlib import Path

from warranted import DEFAULT_WORKER_IMAGE, CheckContext, Verdict, VerdictStatus

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


class CsvDomain:
    name = "csv-transformation"
    version = "0.1"
    worker_image = DEFAULT_WORKER_IMAGE
    checkers = {"transformation": Transformation()}
    sources = (M2_SOURCE,)  # loaded with runpy, so not found by following imports
