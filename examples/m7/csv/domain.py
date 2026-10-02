"""Prototype CSV domain: the M2 transformation evaluator behind the M7 checker shape."""

import json
import runpy
from pathlib import Path

from warranted.experimental import CheckContext, Verdict, VerdictStatus
from warranted.sandbox import IMAGE

HERE = Path(__file__).resolve().parent
M2 = runpy.run_path(str(HERE.parent.parent / "m2/experiments.py"))


class Transformation:
    """Four independent obligations. Feedback names failed obligations only."""

    version = "1"

    def check(self, ctx: CheckContext) -> Verdict:
        offset = next(name for name in ctx.inputs if name.startswith("offset-"))
        reference = json.loads(ctx.private["references.json"])[offset]
        versions = json.loads(ctx.private["versions.json"])
        equality = versions["definition-v1"]["equality"]
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


class CsvDomain:
    name = "csv-transformation"
    version = "0.1"
    worker_image = IMAGE
    checkers = {"transformation": Transformation()}
