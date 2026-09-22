"""Formal uniqueness support must preserve independent task failures."""

import json
import os
import runpy
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "examples/m4/demo.py"


def module():
    return runpy.run_path(str(SCRIPT))


def test_empty_and_dropped_candidates_have_all_premises_but_fail_task_checks():
    demo = module()
    fixture = SCRIPT.parent.parent / "m2/fixture"
    source = (fixture / "input.csv").read_bytes()
    candidates = json.loads((fixture / "candidates.json").read_bytes())
    reference = json.loads((fixture / "references.json").read_bytes())["offset-v1"]
    for name, selection in (
        ("correct", [0, 1, 2]),
        ("dropped-row", [0, 1]),
        ("empty", []),
    ):
        premises = demo["premises"](
            source, candidates[name], selection, demo["MAPPING"]
        )
        assert all(premises[field] is True for field in demo["FIELDS"])
        result = demo["M2"]["evaluate"](
            demo["M2"]["read_rows"](source), candidates[name], reference, "exact"
        )
        assert result["preserved_rows"] is (name == "correct")
        assert result["daily_totals"] is (name == "correct")


def test_current_but_rejected_or_unproved_premise_cannot_support_application():
    from warranted.acceptance import Status
    from warranted.claims import Applicability, Assessment

    demo = module()
    passing = Assessment(Status.PASSED, Applicability.CURRENT, {})
    for status in (
        Status.REJECTED,
        Status.UNPROVED,
        Status.UNKNOWN,
        Status.UNSUPPORTED,
    ):
        assessments = dict.fromkeys(("theorem", *demo["FIELDS"]), passing)
        assessments["unique_input"] = Assessment(status, Applicability.CURRENT, {})
        assert demo["support_status"](assessments) != "supported"
    assert demo["support_status"]({"theorem": passing}) != "supported"


def test_wrong_correspondence_unknown_mapping_and_invalid_selection_block_support():
    demo = module()
    data = b"id,timestamp,value\nr1,2026-01-01T00:30:00,7\n"
    candidate = {"rows": [["other", "irrelevant", 7]], "totals": {}}
    assert (
        demo["premises"](data, candidate, [0], demo["MAPPING"])["correspondence"]
        is False
    )
    candidate["rows"][0][0] = "r1"
    assert (
        demo["premises"](data, candidate, [0], {"function": "unknown"})["injective"]
        is False
    )
    for selection in ([-1], [True], [1], "all"):
        with pytest.raises(ValueError, match="selection"):
            demo["premises"](data, candidate, selection, demo["MAPPING"])


def scripted(root, monkeypatch):
    from warranted import proofs
    from warranted.proofs import ProofStatus, Verification

    demo = module()
    bundle = root.parent / "bundle.json"
    bundle.write_text(
        json.dumps(
            {
                "policy": proofs.policy_digest(),
                "image": "sha256:" + "0" * 64,
                "toolchain": json.loads(
                    (proofs.RESOURCES / "toolchain.json").read_bytes()
                ),
                "manifest": {},
                "build_elapsed_ns": 123,
            }
        )
    )

    def verify(data, captured, *, seconds):
        identity, raw = proofs._inputs(data, captured, seconds)
        identity["podman"] = {"Version": "scripted"}
        status = (
            ProofStatus.UNPROVED
            if b"by skip" in data or b"IO.sleep" in data
            else ProofStatus.PROVED
        )
        raw.update(
            {
                "verdict.json": json.dumps(
                    {"status": status.value, "diagnostic": "scripted", "axioms": []}
                ).encode(),
                "solution.ndjson": b"scripted export",
                "challenge.ndjson": b"scripted target",
            }
        )
        return Verification(status, "scripted", (), identity, 100, raw)

    monkeypatch.setattr(proofs, "_verify", verify)
    demo["initialize"](root, bundle)
    return demo, bundle


def assert_completed(report):
    assert report["spent"] == {"proof": 3, "synthetic-work": 8}
    assert report["reserved"] == {"proof": 0, "synthetic-work": 0}
    assert report["proof_executions"] == 3
    assert report["checker_executions"] == 8
    for name, value in report["matrix"].items():
        application = value["proof_application"]
        assert application["status"] == "supported"
        assert all(
            a["validation"] == "passed" and a["applicability"] == "current"
            for a in application["evidence"].values()
        )
        assert value["decision"] == ("accepted" if name == "correct" else "rejected")
        assert value["checks"] == {
            "unique_ids": True,
            "preserved_rows": name == "correct",
            "utc_timestamps": True,
            "daily_totals": name == "correct",
        }
    changed = report["premise_revision"]
    assert changed["previous"]["status"] == "stale"
    assert changed["current"]["status"] == "unsupported"
    for when in ("previous", "current"):
        assert changed[when]["evidence"]["theorem"]["validation"] == "passed"
        assert changed[when]["evidence"]["theorem"]["applicability"] == "current"
    assert changed["current"]["evidence"]["unique_input"]["validation"] == "rejected"
    assert changed["current"]["evidence"]["unique_input"]["applicability"] == "current"
    assert changed["current"]["evidence"]["injective"]["validation"] == "passed"
    assert changed["current"]["evidence"]["correspondence"]["validation"] == "passed"
    assert Path(report["export"]).is_file()


def test_three_applications_and_premise_revision_keep_results_and_costs(
    tmp_path, monkeypatch
):
    from warranted.ledger import Ledger

    root = tmp_path / "demo"
    demo, bundle = scripted(root, monkeypatch)
    first = demo["demonstrate"]("start", root, bundle)
    assert first["checker_executions"] == 0
    assert first["spent"] == {"proof": 3, "synthetic-work": 0}
    assert [
        first["proof_attempts"][name]["status"]
        for name in ("control", "incomplete", "timeout")
    ] == ["passed", "unproved", "unproved"]
    resumed = demo["demonstrate"]("resume", root, bundle)
    assert_completed(resumed)
    assert resumed["new_executions"] == 8
    repeated = demo["demonstrate"]("resume", root, bundle)
    assert_completed(repeated)
    assert repeated["new_executions"] == 0
    with Ledger.open(root / "ledger") as ledger:
        decisions = [
            json.loads(ledger.read_artifact(o.artifacts["decision.json"]))
            for o in ledger.history()
            if o.origin.kind == "decision"
        ]
        assert len(decisions) == 3
        assert sum(d["status"] == "rejected" for d in decisions) == 2
        assert all(
            d["requirements"]["explicit_source_offsets"] == "excepted"
            for d in decisions
        )
        assert all(
            d["requirements"]["preserved_rows"]
            == d["requirements"]["daily_totals"]
            == "rejected"
            for d in decisions
            if d["status"] == "rejected"
        )


@pytest.mark.parametrize(
    "forgery", ["worker-record", "wrong-field", "wrong-candidate", "ordinary-theorem"]
)
def test_application_cannot_substitute_other_passing_evidence(
    tmp_path, monkeypatch, forgery
):
    from dataclasses import asdict

    from warranted.acceptance import Evidence, _digest
    from warranted.ledger import Ledger, Origin

    root = tmp_path / "demo"
    demo, bundle = scripted(root, monkeypatch)
    demo["demonstrate"]("start", root, bundle)
    report = demo["demonstrate"]("resume", root, bundle)
    original = Evidence.restored(report["premise_revision"]["current"]["application"])
    with Ledger.open(root / "ledger") as ledger:
        host = demo["M2"]["Experiment"](ledger, ledger.start_session(), root)
        body = host.read(original)
        theorem = Evidence.restored(body["claims"]["theorem"])
        if forgery == "wrong-field":
            body["claims"]["unique_input"] = body["claims"]["injective"]
        elif forgery == "wrong-candidate":
            body["inputs"]["candidate"] = asdict(host.ref("candidate/correct"))
        elif forgery == "ordinary-theorem":
            body["claims"]["theorem"] = body["claims"]["injective"]
            theorem = Evidence.restored(body["claims"]["theorem"])
        refs = {
            name: Evidence.restored(ref)
            for section in ("inputs", "claims")
            for name, ref in body[section].items()
        }
        event = ledger.record(
            host.session,
            Origin(
                "m4-application/" + _digest(body),
                "proof-application",
                "worker" if forgery == "worker-record" else "m4-fixture",
                "1",
                host.origins(refs),
            ),
            {"application.json": demo["M2"]["encode"](body)},
        )
        with pytest.raises(ValueError, match="application"):
            demo["support"](
                host,
                Evidence.captured(event, "application.json"),
                theorem,
                host.ref("input-duplicates.csv"),
            )


def test_premise_revision_requires_the_pinned_owner_transition(tmp_path, monkeypatch):
    from warranted.acceptance import Evidence
    from warranted.ledger import Ledger

    root = tmp_path / "demo"
    demo, bundle = scripted(root, monkeypatch)
    demo["demonstrate"]("start", root, bundle)
    report = demo["demonstrate"]("resume", root, bundle)
    with Ledger.open(root / "ledger") as ledger:
        host = demo["M2"]["Experiment"](ledger, ledger.start_session(), root)
        original = Evidence.restored(
            report["matrix"]["correct"]["proof_application"]["application"]
        )
        with pytest.raises(ValueError, match="not approved"):
            demo["revise"](host, original, host.ref("input.csv"))
        assert (
            len([o for o in ledger.history() if o.origin.kind == "premise-revision"])
            == 1
        )


def test_changed_fixture_and_unknown_verification_cannot_dispatch_on_resume(
    tmp_path, monkeypatch
):
    from warranted import proofs
    from warranted.ledger import CorruptArtifact, Ledger
    from warranted.worker import UnknownOutcome

    root = tmp_path / "demo"
    demo, bundle = scripted(root, monkeypatch)

    def interrupted(*args, **kwargs):
        raise RuntimeError("host lost its verifier outcome")

    monkeypatch.setattr(proofs, "_verify", interrupted)
    with pytest.raises(RuntimeError, match="lost"):
        demo["demonstrate"]("start", root, bundle)
    with pytest.raises(UnknownOutcome):
        demo["demonstrate"]("resume", root, bundle)
    with Ledger.open(root / "ledger") as ledger:
        assert ledger.accounting()["proof"].reserved == 1
        assert ledger.accounting()["proof"].spent == 0
        ref = ledger.project.snapshots["m4-intent.json"].artifact
        (ledger.root / "artifacts/sha256" / ref.digest).write_bytes(
            b"unapproved change"
        )
    with pytest.raises(CorruptArtifact):
        demo["demonstrate"]("resume", root, bundle)
    assert len((root / "proof-executions.jsonl").read_bytes().splitlines()) == 1


def test_cli_kills_host_before_ledger_teardown(tmp_path, monkeypatch):
    import signal
    import sys

    from warranted.ledger import Ledger

    root = tmp_path / "demo"
    demo, bundle = scripted(root, monkeypatch)
    events = []
    close = Ledger.close

    def closed(ledger):
        events.append("close")
        close(ledger)

    def killed(pid, sig):
        assert (pid, sig) == (os.getpid(), signal.SIGKILL)
        events.append("kill")
        raise SystemExit(sig)

    monkeypatch.setattr(Ledger, "close", closed)
    monkeypatch.setattr(os, "kill", killed)
    monkeypatch.setattr(
        sys,
        "argv",
        [str(SCRIPT), "start", str(root), "--bundle", str(bundle), "--crash"],
    )
    with pytest.raises(SystemExit, match=str(signal.SIGKILL)):
        demo["main"]()
    assert events == ["kill", "close"]


@pytest.mark.proof
@pytest.mark.skipif(
    os.environ.get("WARRANTED_PROOF_TESTS") != "1",
    reason="requires native proof isolation and the pinned bundle",
)
def test_native_proof_capture_survives_host_kill_before_applications(tmp_path):
    import signal
    import subprocess
    import sys

    root = Path(
        os.environ.get("WARRANTED_M4_REPORTS", tmp_path / "native-demo")
    ).resolve()
    bundle = Path(os.environ["WARRANTED_PROOF_BUNDLE"]).resolve()

    def invoke(stage, *args):
        return subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                stage,
                str(root),
                "--bundle",
                str(bundle),
                *args,
            ],
            capture_output=True,
            text=True,
            timeout=90,
        )

    first = invoke("start", "--crash")
    assert first.returncode == -signal.SIGKILL, first.stderr
    initial = json.loads((root / "reports/start.json").read_bytes())
    assert initial["checker_executions"] == 0
    assert initial["proof_executions"] == 3
    for expected_new in (8, 0):
        resumed = invoke("resume")
        assert resumed.returncode == 0, resumed.stderr
        report = json.loads(resumed.stdout)
        assert_completed(report)
        assert report["new_executions"] == expected_new
        assert report["proof_attempts"] == initial["proof_attempts"]
