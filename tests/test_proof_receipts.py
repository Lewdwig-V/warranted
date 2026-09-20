"""Proof evidence cannot transfer between requests or settle an unknown attempt."""

import json
import multiprocessing

import pytest

from warranted.acceptance import Evidence, Status
from warranted.claims import Claims
from warranted.ledger import Ledger, Manifest, Snapshot
from warranted.proof_receipts import Proofs
from warranted.proofs import RESOURCES, ProofStatus, Verification, policy_digest


def setup(root):
    bundle = root.parent / "bundle.json"
    bundle.write_text(
        json.dumps(
            {
                "policy": policy_digest(),
                "image": "sha256:" + "0" * 64,
                "toolchain": json.loads((RESOURCES / "toolchain.json").read_bytes()),
                "manifest": {},
            }
        )
    )
    ledger = Ledger.create(
        root,
        Manifest("proofs", "1", "run", "world", {}, {"proof": 2}),
        {
            "solution": Snapshot(b"proposed Lean source", "test", "1"),
            "other": Snapshot(b"different Lean source", "test", "1"),
        },
    )
    return ledger, bundle


def source(ledger, name="solution"):
    return Evidence(name, ledger.project.snapshots[name].artifact)


def boundary(monkeypatch, status=ProofStatus.PROVED, *, forge=False):
    from warranted import proofs

    def verify(data, bundle, *, seconds):
        identity, raw = proofs._inputs(data, bundle, seconds)
        identity["podman"] = {"Version": "test"}
        if forge:
            identity["solution"] = "0" * 64
        raw.update(
            stdout=b"diagnostic",
            stderr=b"",
            **{
                "verdict.json": json.dumps(
                    {"status": status.value, "diagnostic": "test", "axioms": []}
                ).encode(),
                "solution.ndjson": b"serialized solution",
                "challenge.ndjson": b"serialized challenge",
            },
        )
        return Verification(status, "test", (), identity, 123, raw)

    monkeypatch.setattr(proofs, "_verify", verify)


@pytest.mark.parametrize(
    "status, expected",
    [
        (ProofStatus.REJECTED, Status.REJECTED),
        (ProofStatus.UNPROVED, Status.UNPROVED),
        (ProofStatus.INFRASTRUCTURE_FAILURE, Status.INFRASTRUCTURE_FAILURE),
        (ProofStatus.UNSUPPORTED, Status.UNSUPPORTED),
    ],
)
def test_exact_outcomes_and_costs_survive_restart(
    tmp_path, monkeypatch, status, expected
):
    boundary(monkeypatch, status)
    ledger, bundle = setup(tmp_path / "ledger")
    with ledger:
        session = ledger.start_session()
        proofs = Proofs(ledger, session, bundle, seconds=5)
        request = proofs.check("attempt", source(ledger))
        claim = Claims(ledger, session).record(
            "The conditional theorem.",
            proofs.target,
            {},
            validation=(request, "proof"),
            complete=True,
        )
        assert Claims(ledger, session).assess(claim, {}).validation is expected
    with Ledger.open(tmp_path / "ledger") as ledger:
        assert (
            Claims(ledger, ledger.start_session()).assess(claim, {}).validation
            is expected
        )
        balance = ledger.accounting()["proof"]
        if status is ProofStatus.UNSUPPORTED:
            assert ledger.lookup(request).state == "unknown"
            assert (balance.spent, balance.reserved) == (0, 1)
        else:
            assert ledger.lookup(request).completion.result.elapsed_ns == 123
            assert (balance.spent, balance.reserved) == (1, 0)


def test_forged_identity_never_completes_or_releases_reservation(tmp_path, monkeypatch):
    boundary(monkeypatch, forge=True)
    ledger, bundle = setup(tmp_path / "ledger")
    with ledger:
        session = ledger.start_session()
        proofs = Proofs(ledger, session, bundle)
        request = proofs.check("attempt", source(ledger))
        claim = Claims(ledger, session).record(
            "The conditional theorem.",
            proofs.target,
            {},
            validation=(request, "proof"),
            complete=True,
        )
        assert (
            Claims(ledger, session).assess(claim, {}).validation is Status.UNSUPPORTED
        )
        assert ledger.lookup(request).state == "unknown"
        assert ledger.accounting()["proof"].reserved == 1
    with Ledger.open(tmp_path / "ledger") as ledger:
        assert (
            Claims(ledger, ledger.start_session()).assess(claim, {}).validation
            is Status.UNSUPPORTED
        )
        assert ledger.lookup(request).state == "unknown"


def test_wrong_target_or_copied_receipt_cannot_establish_proof(tmp_path, monkeypatch):
    from warranted.ledger import Outcome, Result
    from warranted.proof_receipts import proof_status

    boundary(monkeypatch)
    ledger, bundle = setup(tmp_path / "ledger")
    with ledger:
        session = ledger.start_session()
        proofs = Proofs(ledger, session, bundle)
        first = proofs.check("first", source(ledger))
        assert proof_status(ledger, first, proofs.target) is Status.PASSED
        # Even another input of the same request is not the theorem target.
        assert proof_status(ledger, first, source(ledger)) is Status.UNSUPPORTED
        other = proofs.request("other", source(ledger, "other"))
        ledger.reserve(session, other, {"proof": 1})
        assert ledger.begin(session, other)
        original = ledger.lookup(first).completion
        raw = {
            name: ledger.read_artifact(ref)
            for name, ref in original.observation.artifacts.items()
        }
        ledger.complete(
            session, other, Result(Outcome.SUCCEEDED, 0, {"proof": 1}, 123), raw
        )
        assert proof_status(ledger, other, proofs.target) is Status.UNSUPPORTED
        target = proofs.target
    with Ledger.open(tmp_path / "ledger") as ledger:
        assert proof_status(ledger, first, target) is Status.PASSED
        assert proof_status(ledger, other, target) is Status.UNSUPPORTED


@pytest.mark.parametrize(
    "change",
    ["solution", "limits", "kernel", "runtime", "bundle", "challenge", "policy"],
)
def test_changed_request_cannot_reuse_operation_id(tmp_path, monkeypatch, change):
    from warranted import proofs as verifier
    from warranted.ledger import OperationConflict

    boundary(monkeypatch)
    ledger, bundle = setup(tmp_path / "ledger")
    with ledger:
        session = ledger.start_session()
        proofs = Proofs(ledger, session, bundle)
        proofs.check("attempt", source(ledger))
        if change == "kernel":
            monkeypatch.setattr(verifier.platform, "release", lambda: "changed kernel")
        elif change == "runtime":
            runtime = tmp_path / "different-podman"
            runtime.write_bytes(b"different host runtime")
            monkeypatch.setattr(verifier.shutil, "which", lambda _: str(runtime))
        elif change == "bundle":
            bundle.write_bytes(bundle.read_bytes() + b"\n")
        elif change == "challenge":
            resources = tmp_path / "proof"
            resources.mkdir()
            for path in RESOURCES.iterdir():
                if path.is_file():
                    (resources / path.name).write_bytes(path.read_bytes())
            (resources / "Challenge.lean").write_bytes(
                b"changed target and definitions"
            )
            monkeypatch.setattr(verifier, "RESOURCES", resources)
        elif change == "policy":
            monkeypatch.setattr(verifier, "policy_digest", lambda: "new policy")
        if change in ("challenge", "policy"):
            data = json.loads(bundle.read_bytes())
            data["policy"] = verifier.policy_digest()
            bundle.write_text(json.dumps(data))
        current = Proofs(
            ledger, session, bundle, seconds=5 if change == "limits" else 120
        )
        with pytest.raises(OperationConflict):
            current.check(
                "attempt",
                source(ledger, "other" if change == "solution" else "solution"),
            )
        assert ledger.accounting()["proof"].spent == 1


def test_changed_project_context_cannot_reuse_receipt(tmp_path, monkeypatch):
    from dataclasses import replace

    from warranted.ledger import OperationConflict

    boundary(monkeypatch)
    ledger, bundle = setup(tmp_path / "ledger")
    with ledger:
        proofs = Proofs(ledger, ledger.start_session(), bundle)
        request = proofs.check("attempt", source(ledger))
        changed = replace(
            request.context,
            manifest=replace(request.context.manifest, environment={"host": "other"}),
        )
        with pytest.raises(OperationConflict):
            ledger.lookup(replace(request, context=changed))


def test_corrupt_raw_export_blocks_reuse_without_running_again(tmp_path, monkeypatch):
    from warranted.ledger import CorruptArtifact
    from warranted.proof_receipts import proof_status

    boundary(monkeypatch)
    ledger, bundle = setup(tmp_path / "ledger")
    with ledger:
        proofs = Proofs(ledger, ledger.start_session(), bundle)
        request = proofs.check("attempt", source(ledger))
        ref = ledger.lookup(request).completion.observation.artifacts["solution.ndjson"]
        target = proofs.target
    path = tmp_path / "ledger" / "artifacts" / "sha256" / ref.digest
    path.write_bytes(b"corrupt")
    with Ledger.open(tmp_path / "ledger") as ledger:
        proofs = Proofs(ledger, ledger.start_session(), bundle)
        with pytest.raises(CorruptArtifact):
            proofs.check("attempt", source(ledger))
        assert proof_status(ledger, request, target) is Status.UNSUPPORTED
        assert ledger.accounting()["proof"].spent == 1


def test_budget_exhaustion_blocks_verifier_but_allows_exact_reuse(
    tmp_path, monkeypatch
):
    from warranted.ledger import BudgetExceeded

    boundary(monkeypatch, ProofStatus.UNPROVED)
    ledger, bundle = setup(tmp_path / "ledger")
    with ledger:
        proofs = Proofs(ledger, ledger.start_session(), bundle)
        first = proofs.check("one", source(ledger))
        proofs.check("two", source(ledger, "other"))
        assert proofs.check("one", source(ledger)) == first
        with pytest.raises(BudgetExceeded):
            proofs.check("three", source(ledger))
        assert ledger.accounting()["proof"].spent == 2


def test_unknown_blocks_retry_and_new_attempt_even_with_forged_success(
    tmp_path, monkeypatch
):
    from warranted.proof_receipts import proof_status
    from warranted.worker import UnknownOutcome

    boundary(monkeypatch)
    ledger, bundle = setup(tmp_path / "ledger")
    with ledger:
        session = ledger.start_session()
        proofs = Proofs(ledger, session, bundle)
        request = proofs.request("attempt", source(ledger))
        ledger.reserve(session, request, {"proof": 1})
        assert ledger.begin(session, request)
        assert proof_status(ledger, request, proofs.target) is Status.UNKNOWN
        ledger.record(
            session, request.origin, {"verification.json": b'{"status":"proved"}'}
        )
        assert proof_status(ledger, request, proofs.target) is Status.UNSUPPORTED
    with Ledger.open(tmp_path / "ledger") as ledger:
        proofs = Proofs(ledger, ledger.start_session(), bundle)
        assert proofs.check("attempt", source(ledger)) == request
        assert proof_status(ledger, request, proofs.target) is Status.UNSUPPORTED
        with pytest.raises(UnknownOutcome):
            proofs.check("new-attempt", source(ledger))
        assert ledger.accounting()["proof"].spent == 0
        assert ledger.lookup(request).state == "unknown"


def test_bundle_file_changes_cannot_change_captured_verification(tmp_path, monkeypatch):
    from warranted.proof_receipts import proof_status

    boundary(monkeypatch)
    ledger, bundle = setup(tmp_path / "ledger")
    with ledger:
        proofs = Proofs(ledger, ledger.start_session(), bundle)
        bundle.write_bytes(b"not the captured bundle")
        request = proofs.check("attempt", source(ledger))
        assert proof_status(ledger, request, proofs.target) is Status.PASSED


def _host(root, bundle, point, pipe):
    from warranted import proofs as verifier

    def pause():
        pipe.send("ready")
        pipe.recv()

    with pytest.MonkeyPatch.context() as patch, Ledger.open(root) as ledger:
        boundary(patch)
        fake = verifier._verify

        def execute(*args, **kwargs):
            if point == "begun":
                pause()
            with (root.parent / "executions").open("ab") as stream:
                stream.write(b"verification\n")
            result = fake(*args, **kwargs)
            if point == "executed":
                pause()
            return result

        patch.setattr(verifier, "_verify", execute)
        if point in ("reserved", "completed"):
            method = "reserve" if point == "reserved" else "complete"
            original = getattr(ledger, method)

            def stop_after_commit(*args, **kwargs):
                result = original(*args, **kwargs)
                pause()
                return result

            patch.setattr(ledger, method, stop_after_commit)
        session = ledger.start_session()
        proofs = Proofs(ledger, session, bundle, seconds=5)
        request = proofs.request("attempt", source(ledger))
        claim = Claims(ledger, session).record(
            "The conditional theorem.",
            proofs.target,
            {},
            validation=(request, "proof"),
            complete=True,
        )
        assert proofs.check("attempt", source(ledger)) == request
        balance = ledger.accounting()["proof"]
        pipe.send(
            {
                "state": ledger.lookup(request).state,
                "validation": Claims(ledger, session)
                .assess(claim, {})
                .validation.value,
                "spent": balance.spent,
                "reserved": balance.reserved,
            }
        )


def _process(root, bundle, point):
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe()
    process = context.Process(target=_host, args=(root, bundle, point, child))
    process.start()
    try:
        assert parent.poll(15), "host did not reach the expected boundary"
        result = parent.recv()
        if point is not None:
            assert result == "ready"
            process.kill()
        process.join(15)
        assert not process.is_alive()
        if point is None:
            assert process.exitcode == 0
        return result
    finally:
        if process.is_alive():
            process.kill()
        process.join()
        parent.close()
        child.close()


@pytest.mark.parametrize("point", ["reserved", "begun", "executed", "completed"])
def test_killed_host_reuses_completed_work_and_retains_unknown_budget(tmp_path, point):
    root = tmp_path / "ledger"
    ledger, bundle = setup(root)
    ledger.close()
    _process(root, bundle, point)
    expected = (
        {"state": "completed", "validation": "passed", "spent": 1, "reserved": 0}
        if point in ("reserved", "completed")
        else {"state": "unknown", "validation": "unknown", "spent": 0, "reserved": 1}
    )
    assert _process(root, bundle, None) == expected
    assert _process(root, bundle, None) == expected
    witness = tmp_path / "executions"
    assert (witness.read_bytes() if witness.exists() else b"") == (
        b"" if point == "begun" else b"verification\n"
    )


def test_historical_claim_needs_no_live_tools_and_keeps_validation_when_stale(
    tmp_path, monkeypatch
):
    from warranted import proofs as verifier

    boundary(monkeypatch)
    ledger, bundle = setup(tmp_path / "ledger")
    with ledger:
        session = ledger.start_session()
        proofs = Proofs(ledger, session, bundle)
        request = proofs.check("attempt", source(ledger))
        claim = Claims(ledger, session).record(
            "The conditional theorem under a declared application assumption.",
            proofs.target,
            {"premise": source(ledger)},
            validation=(request, "proof"),
            complete=True,
        )
    bundle.unlink()

    def unavailable(*args, **kwargs):
        raise AssertionError("historical assessment must not use live verification")

    monkeypatch.setattr(verifier, "_inputs", unavailable)
    monkeypatch.setattr(verifier, "_verify", unavailable)
    with Ledger.open(tmp_path / "ledger") as ledger:
        claims = Claims(ledger, ledger.start_session())
        report = claims.assess(claim, {"premise": source(ledger, "other")})
        assert report.validation is Status.PASSED
        assert report.applicability.value == "stale"
        assert ledger.accounting()["proof"].spent == 1


def test_runtime_unavailable_is_attributable_and_cleanup_uncertainty_is_not(
    tmp_path, monkeypatch
):
    from warranted import proofs as verifier
    from warranted.containers import SandboxFailure
    from warranted.proof_receipts import proof_status

    ledger, bundle = setup(tmp_path / "ledger")

    def unavailable(*args, **kwargs):
        raise SandboxFailure("runtime unavailable")

    monkeypatch.setattr(verifier, "require_runtime", unavailable)
    with ledger:
        proofs = Proofs(ledger, ledger.start_session(), bundle)
        known = proofs.check("known", source(ledger))
        assert (
            proof_status(ledger, known, proofs.target) is Status.INFRASTRUCTURE_FAILURE
        )
        monkeypatch.setattr(verifier, "_verify", unavailable)
        with pytest.raises(SandboxFailure):
            proofs.check("uncertain", source(ledger))
        unknown = proofs.request("uncertain", source(ledger))
        assert proof_status(ledger, unknown, proofs.target) is Status.UNKNOWN
        balance = ledger.accounting()["proof"]
        assert (balance.spent, balance.reserved) == (1, 1)
