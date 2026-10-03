"""Prototype migration domain: the M5 configuration checker behind the M7 checker shape.

Each reference case runs in its own contained job, so cases share no process or
filesystem. The private `contract.json` names the obligations that gate
acceptance; a revision replaces it. Feedback shows only those obligations, so a
result the contract does not require stays host-only.
"""

import json
import runpy
from pathlib import Path

from warranted import DEFAULT_WORKER_IMAGE, CheckContext, Verdict, VerdictStatus

HERE = Path(__file__).resolve().parent
M5_SOURCE = HERE.parent.parent / "m5/demo.py"
M5 = runpy.run_path(str(M5_SOURCE))
OBLIGATIONS = (
    "repository_integrity",
    "output_schema",
    "renaming",
    "legacy_label",
    "input_rejection",
    "repetition",
)


class Migration:
    version = "1"
    isolated = True  # contained jobs over the candidate and task bytes only

    def check(self, ctx: CheckContext) -> Verdict:
        required = json.loads(ctx.private["contract.json"])["obligations"]
        if not required or not set(required) <= set(OBLIGATIONS):
            raise ValueError(f"contract names an unknown obligation: {required}")
        cases = json.loads(ctx.private["references.json"])
        try:
            source = M5["candidate_source"](ctx.candidate["result.json"])
        except KeyError:
            source, error = None, "result.json is missing"
        except (ValueError, RecursionError) as invalid:
            source, error = None, str(invalid)
        # An invalid patch is never executed, so it establishes nothing.
        results = dict.fromkeys(OBLIGATIONS, source is not None)
        detail = {}
        for name, case in cases.items() if source is not None else ():
            job = ctx.run_job(
                DEFAULT_WORKER_IMAGE,
                ["python", "-I", "migrate.py"],
                {"migrate.py": source},
                stdin=case["input"].encode(),
                timeout_seconds=M5["CASE_SECONDS"],
            )
            values = M5["judge"](
                case, job.stdout, job.returncode, job.timed_out or job.truncated
            )
            for obligation, passed in values.items():
                results[obligation] = results[obligation] and passed
            detail[name] = values
        shown = {name: results[name] for name in required}
        status = VerdictStatus.PASSED if all(shown.values()) else VerdictStatus.REJECTED
        feedback = {"obligations": shown}
        if source is None:
            feedback["error"] = error
        return Verdict(status, feedback, {"obligations": results, "cases": detail})


class MigrationDomain:
    name = "config-migration"
    version = "0.1"
    worker_image = DEFAULT_WORKER_IMAGE
    checkers = {"migration": Migration()}
    sources = (M5_SOURCE,)  # loaded with runpy, so not found by following imports
