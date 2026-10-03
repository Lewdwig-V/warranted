"""The `warranted` command. It uses only the public API in `warranted`."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.util
import json
import os
import sys
import tomllib
from pathlib import Path

from warranted import (
    LocalChatCompletions,
    Project,
    RunConfig,
    RunOutcome,
    RunResult,
    RunStatus,
    TaskSpec,
    __version__,
)

PROJECT_FILE = "warranted.toml"
JSON_VERSION = 1
USAGE = 2
EXIT = {
    RunOutcome.ACCEPTED: 0,
    RunOutcome.REJECTED: 1,
    RunOutcome.INCOMPLETE: 1,
    RunOutcome.UNKNOWN: 3,
    RunOutcome.UNSUPPORTED: 4,
    RunOutcome.INFRASTRUCTURE_FAILURE: 5,
}
ADAPTER_FIELDS = {"base_url", "max_tokens", "timeout_seconds", "seed", "model_digest"}
LOCAL_URL = "http://127.0.0.1:11434/v1"


class UsageError(Exception):
    """Input the command refuses; exit status 2."""


def load_domain(reference: str, base: Path):
    """Load `package.module:Object` or `path/to/domain.py:Object`.

    A class is instantiated without arguments; any other object is used as is.
    """
    module_name, _, attribute = reference.rpartition(":")
    if not module_name or not attribute:
        raise UsageError("domain must be MODULE:OBJECT or PATH.py:OBJECT")
    if module_name.endswith(".py"):
        path = (base / module_name).resolve()
        if not path.is_file():
            raise UsageError(f"domain file not found: {path}")
        name = "warranted_domain_" + hashlib.sha256(bytes(path)).hexdigest()[:16]
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    else:
        try:
            module = importlib.import_module(module_name)
        except ImportError as error:
            message = f"cannot import domain module {module_name}: {error}"
            raise UsageError(message) from error
    found = module
    for part in attribute.split("."):
        if not hasattr(found, part):
            raise UsageError(f"{module_name} has no {attribute}")
        found = getattr(found, part)
    return found() if isinstance(found, type) else found


def open_project(directory: str) -> Project:
    root = Path(directory)
    path = root / PROJECT_FILE
    if not path.is_file():
        raise UsageError(f"not a warranted project (no {PROJECT_FILE}): {root}")
    reference = tomllib.loads(path.read_text()).get("domain")
    if type(reference) is not str:
        raise UsageError(f"{path} must set domain")
    return Project(root, load_domain(reference, root))


def load_run_config(path: str) -> tuple[RunConfig, LocalChatCompletions]:
    data = tomllib.loads(Path(path).read_text())
    known = {"model", "provider", "max_steps", "budgets", "adapter"}
    if not data.keys() <= known or "model" not in data:
        raise UsageError(f"run configuration needs model, and only {sorted(known)}")
    provider = data.get("provider", "ollama")
    if provider == "openrouter":
        raise UsageError(
            "provider openrouter is not supported by the task layer yet: it needs "
            "a pinned runtime the task layer does not record"
        )
    if provider != "ollama":
        raise UsageError(f"unknown provider: {provider}")
    adapter = dict(data.get("adapter", {}))
    if not adapter.keys() <= ADAPTER_FIELDS:
        raise UsageError(f"adapter settings are limited to {sorted(ADAPTER_FIELDS)}")
    base_url = adapter.pop("base_url", LOCAL_URL)
    model = LocalChatCompletions(base_url, data["model"], **adapter)
    config = RunConfig(data["model"], data.get("max_steps", 4), data.get("budgets", {}))
    return config, model


def submissions_view(submissions) -> list[dict]:
    return [
        {
            "index": s.index,
            "decision": s.decision,
            "verdicts": {name: str(v) for name, v in s.verdicts.items()},
        }
        for s in submissions
    ]


def result_view(result: RunResult) -> dict:
    return {
        "version": JSON_VERSION,
        "run": result.run_id,
        "outcome": str(result.outcome),
        "submissions": submissions_view(result.submissions),
        "detail": result.detail,
    }


def status_view(status: RunStatus) -> dict:
    return {
        "version": JSON_VERSION,
        "run": status.run_id,
        "task": status.task_id,
        "model": status.model,
        "outcome": None if status.outcome is None else str(status.outcome),
        "submissions_allowed": status.submissions_allowed,
        "submissions": submissions_view(status.submissions),
        "blocked": list(status.blocked),
        "accounting": {
            unit: {"limit": b.limit, "spent": b.spent, "reserved": b.reserved}
            for unit, b in sorted(status.accounting.items())
        },
    }


def emit(args, view: dict, text: str) -> None:
    print(json.dumps(view, indent=2, sort_keys=True) if args.json else text)


def outcome_text(view: dict) -> str:
    decisions = ", ".join(s["decision"] for s in view["submissions"]) or "none"
    line = f"{view['run']}: {view['outcome']} (submissions: {decisions})"
    return line + (f"\n{view['detail']}" if view.get("detail") else "")


def cmd_init(args) -> int:
    root = Path(args.dir)
    if root.exists():
        raise UsageError(f"already exists: {root}")
    domain = load_domain(args.domain, Path.cwd())
    allowances = {}
    for item in args.allow:
        unit, _, value = item.partition("=")
        if not unit or not value.isdigit():
            raise UsageError(f"--allow takes UNIT=N, not {item!r}")
        allowances[unit] = int(value)
    reference = args.domain
    module_name, _, attribute = reference.rpartition(":")
    if module_name.endswith(".py"):
        # Stored relative to the project, so the pair can move together.
        relative = os.path.relpath(Path(module_name).resolve(), root.resolve())
        reference = f"{Path(relative).as_posix()}:{attribute}"
    Project.create(root, domain, allowances)
    (root / PROJECT_FILE).write_text(f"domain = {json.dumps(reference)}\n")
    print(f"created {root} for domain {domain.name} {domain.version}")
    return 0


def cmd_run(args) -> int:
    project = open_project(args.dir)
    task = TaskSpec.load(Path(args.task))
    config, model = load_run_config(args.config)
    result = project.start(task, config, model)
    view = result_view(result)
    emit(args, view, outcome_text(view))
    return EXIT[result.outcome]


def cmd_resume(args) -> int:
    project = open_project(args.dir)
    _, model = load_run_config(args.config)
    result = project.resume(args.run, model)
    view = result_view(result)
    emit(args, view, outcome_text(view))
    return EXIT[result.outcome]


def cmd_status(args) -> int:
    project = open_project(args.dir)
    if args.run is None:
        rows = [status_view(project.status(run)) for run in project.runs()]
        text = "\n".join(
            f"{r['run']}  {r['task']}  {r['outcome'] or 'open'}  "
            f"{len(r['submissions'])}/{r['submissions_allowed']} submissions"
            for r in rows
        )
        emit(args, {"version": JSON_VERSION, "runs": rows}, text or "no runs")
        return 0
    view = status_view(project.status(args.run))
    lines = [
        f"run {view['run']} of task {view['task']} with model {view['model']}",
        f"outcome: {view['outcome'] or 'open (resume to continue)'}",
    ]
    lines += [
        f"submission {s['index']}: {s['decision']}"
        + "".join(f", {n} {v}" for n, v in sorted(s["verdicts"].items()))
        for s in view["submissions"]
    ]
    lines += [f"blocked by {op}" for op in view["blocked"]]
    lines += [
        f"{unit}: {b['spent']} spent, {b['reserved']} reserved, limit {b['limit']}"
        for unit, b in view["accounting"].items()
    ]
    emit(args, view, "\n".join(lines))
    return 0


def cmd_memory(args) -> int:
    project = open_project(args.dir)
    task = TaskSpec.load(Path(args.task))
    entries = [
        {
            "run": e.run_id,
            "task": e.task_id,
            "submission": e.submission,
            "source": e.source,
            "tier": e.tier,
            "applicability": str(e.applicability),
            "shown": e.shown,
            "items": list(e.items),
        }
        for e in project.memory(task)
    ]
    text = "\n".join(
        f"{'shown' if e['shown'] else 'withheld'}  {e['tier'] or 'not accepted'}  "
        f"{e['applicability']}  {e['source']}  {e['task']} {e['run']} "
        f"#{e['submission']}  {len(e['items'])} item(s)"
        for e in entries
    )
    if task.memory is None:
        text = "task has no memory scope"
    view = {
        "version": JSON_VERSION,
        "scope": task.memory and task.memory.scope,
        "entries": entries,
    }
    emit(args, view, text or "no entries")
    return 0


def cmd_export(args) -> int:
    project = open_project(args.dir)
    index = project.export(args.run, Path(args.dest))
    print(index)
    return 0


def parser() -> argparse.ArgumentParser:
    top = argparse.ArgumentParser(
        prog="warranted",
        description="Durable, checkable knowledge for long-horizon agent work.",
        epilog=(
            "Exit status for run and resume: 0 accepted, 1 rejected or incomplete, "
            "2 usage error, 3 unknown, 4 unsupported, 5 infrastructure failure."
        ),
    )
    top.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = top.add_subparsers(dest="command", metavar="COMMAND")

    def command(name, handler, help):
        sub = commands.add_parser(name, help=help, description=help)
        sub.set_defaults(handler=handler)
        return sub

    sub = command("init", cmd_init, "Create a project for a domain.")
    sub.add_argument("dir")
    sub.add_argument("--domain", required=True, help="MODULE:OBJECT or PATH.py:OBJECT")
    sub.add_argument(
        "--allow",
        action="append",
        default=[],
        metavar="UNIT=N",
        help="project allowance for a unit, such as model=100 (repeatable)",
    )
    sub = command("run", cmd_run, "Start a new run of a task; prints its ID.")
    sub.add_argument("dir")
    sub.add_argument("task", help="task TOML file")
    sub.add_argument("--config", required=True, help="run configuration TOML file")
    sub.add_argument("--json", action="store_true")
    sub = command("resume", cmd_resume, "Continue a run where it stopped.")
    sub.add_argument("dir")
    sub.add_argument("run")
    sub.add_argument(
        "--config", required=True, help="run configuration; only its model is used"
    )
    sub.add_argument("--json", action="store_true")
    sub = command("status", cmd_status, "Report runs, or one run in detail.")
    sub.add_argument("dir")
    sub.add_argument("run", nargs="?")
    sub.add_argument("--json", action="store_true")
    sub = command("memory", cmd_memory, "List memory entries for a task's scope.")
    sub.add_argument("dir")
    sub.add_argument("task", help="task TOML file")
    sub.add_argument("--json", action="store_true")
    sub = command("export", cmd_export, "Export a run's public records.")
    sub.add_argument("dir")
    sub.add_argument("run")
    sub.add_argument("dest")
    return top


def main(argv: list[str] | None = None) -> int:
    top = parser()
    args = top.parse_args(argv)
    if args.command is None:
        top.print_help()
        return 0
    try:
        return args.handler(args)
    except (UsageError, ValueError, FileNotFoundError, FileExistsError) as error:
        print(f"warranted {args.command}: {error}", file=sys.stderr)
        return USAGE
