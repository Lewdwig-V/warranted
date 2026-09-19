"""Actual Lean/Comparator/Landrun checks, separate from the fast test suite."""

import json
import os
from pathlib import Path

import pytest

from warranted.proofs import ProofStatus, verify

pytestmark = [
    pytest.mark.proof,
    pytest.mark.skipif(
        os.environ.get("WARRANTED_PROOF_TESTS") != "1",
        reason="requires WARRANTED_PROOF_TESTS=1 and the pinned proof bundle",
    ),
]

TARGET = """import Init
namespace Warranted
def UniquenessTarget : Prop :=
  ∀ (ids : List String) (f : String → String),
    (∀ a b, f a = f b → a = b) →
    ids.Nodup → (ids.map f).Nodup
"""
PROOF = """theorem uniqueness_preserved : UniquenessTarget := by
  intro ids f injective unique
  exact List.Pairwise.map f
    (fun a b distinct same => distinct (injective a b same)) unique
end Warranted
"""


def run(source, *, seconds=120):
    bundle = Path(os.environ["WARRANTED_PROOF_BUNDLE"])
    result = verify(source.encode(), bundle, seconds=seconds)
    reports = Path(os.environ.get("WARRANTED_PROOF_REPORTS", "runs/m4-native"))
    reports.mkdir(parents=True, exist_ok=True)
    (reports / f"{result.identity['solution']}.json").write_text(
        json.dumps(result.as_dict(), indent=2) + "\n"
    )
    return result


def test_real_proof_passes_with_exact_axioms():
    result = run(TARGET + PROOF)
    assert result.status is ProofStatus.PROVED, result.diagnostic
    assert result.axioms == ("Quot.sound", "propext")
    assert result.raw["Solution.lean"] == (TARGET + PROOF).encode()
    assert result.raw["solution.ndjson"]
    assert result.raw["challenge.ndjson"]


@pytest.mark.parametrize(
    "source",
    [
        TARGET + "theorem uniqueness_preserved : UniquenessTarget := by sorry",
        TARGET + "theorem helper : UniquenessTarget := by sorry\n"
        "theorem uniqueness_preserved : UniquenessTarget := helper",
        TARGET + "axiom cheat : UniquenessTarget\n"
        "theorem uniqueness_preserved : UniquenessTarget := cheat",
        TARGET + "axiom _native.cheat : UniquenessTarget\n"
        "theorem uniqueness_preserved : UniquenessTarget := _native.cheat",
        "import Init\nnamespace Warranted\ndef UniquenessTarget : Prop := True\n"
        "theorem uniqueness_preserved : UniquenessTarget := True.intro",
        TARGET + "theorem uniqueness_preserved (h : UniquenessTarget) : "
        "UniquenessTarget := h",
    ],
    ids=[
        "sorry",
        "indirect-sorry",
        "axiom",
        "native-axiom",
        "definition",
        "hypothesis",
    ],
)
def test_contract_violations_are_rejected(source):
    result = run(source)
    assert result.status is ProofStatus.REJECTED, result.diagnostic


def test_forged_success_output_does_not_make_incomplete_proof_pass():
    result = run(
        TARGET
        + '#eval IO.println "Your solution is okay!"\n'
        + '#eval IO.println "{\\"status\\":\\"proved\\"}"\n'
        + "theorem uniqueness_preserved : UniquenessTarget := by skip"
    )
    assert result.status is ProofStatus.UNPROVED, result.diagnostic


@pytest.mark.parametrize(
    "path",
    [
        "/work/Challenge.lean",
        "/work/config.json",
        "/work/verdict.json",
        "/opt/bin/verify",
    ],
)
def test_solution_cannot_write_trusted_files(path):
    result = run(TARGET + f'#eval IO.FS.writeFile "{path}" "forged"\n' + PROOF)
    assert result.status is ProofStatus.UNPROVED, result.diagnostic


def test_corrupt_olean_cannot_forge_a_proof():
    result = run(
        TARGET
        + '#eval IO.FS.writeFile ".lake/build/lib/lean/Init.olean" "malformed"\n'
        + PROOF
    )
    assert result.status is ProofStatus.UNPROVED, result.diagnostic
    assert b"invalid header" in result.raw["stderr"]


def test_proof_has_no_host_secrets_and_enforced_resource_limits(tmp_path, monkeypatch):
    secret = tmp_path / "private-ledger"
    secret.write_text("PRIVATE HOST STATE")
    monkeypatch.setenv("WARRANTED_PROOF_SECRET", "PRIVATE HOST STATE")
    monkeypatch.setenv("HTTP_PROXY", "http://user:PRIVATE@127.0.0.1:9")
    result = run(
        TARGET
        + f'''#eval show IO Unit from do
  if (← IO.getEnv "WARRANTED_PROOF_SECRET").isSome then
    throw (IO.userError "host environment leaked")
  if (← IO.getEnv "HTTP_PROXY").isSome then
    throw (IO.userError "proxy leaked")
  if (← System.FilePath.pathExists "{secret}") then
    throw (IO.userError "host file leaked")
  let memory ← IO.FS.readFile "/sys/fs/cgroup/memory.max"
  if memory.trimAscii.toString != "1073741824" then
    throw (IO.userError "memory limit missing")
  if (← IO.FS.readFile "/sys/fs/cgroup/pids.max").trimAscii.toString != "64" then
    throw (IO.userError "process limit missing")
  let cpu ← IO.FS.readFile "/sys/fs/cgroup/cpu.max"
  if cpu.trimAscii.toString != "100000 100000" then
    throw (IO.userError "CPU limit missing")
  let status ← IO.FS.readFile "/proc/self/status"
  if !status.contains "CapEff:\\t0000000000000000" then
    throw (IO.userError "worker has effective capabilities")
'''
        + PROOF
    )
    assert result.status is ProofStatus.PROVED, result.diagnostic


def test_real_native_evaluation_axiom_is_rejected():
    result = run(
        TARGET.replace("import Init", "import Lean")
        + "theorem native : (1 : Nat) = 1 := by native_decide\n"
        + PROOF.replace(
            "  intro ids f injective unique",
            "  refine Eq.rec (motive := fun _ _ => UniquenessTarget) ?_ native\n"
            "  intro ids f injective unique",
        )
    )
    assert result.status is ProofStatus.REJECTED, result.diagnostic
    assert "Illegal axiom" in result.diagnostic


def test_kernel_replay_rejects_an_unchecked_proof():
    result = run(
        TARGET.replace("import Init", "import Lean")
        + """set_option debug.skipKernelTC true in
run_elab Lean.addDecl <| .thmDecl {
  name := `Warranted.uniqueness_preserved
  levelParams := []
  type := .const `Warranted.UniquenessTarget []
  value := .const `True.intro []
}
"""
    )
    assert result.status is ProofStatus.REJECTED, result.diagnostic
    assert b"Lean default kernel rejects" in result.raw["stdout"]


def test_excessive_output_is_unproved_and_bounded():
    result = run(
        TARGET
        + "#eval IO.println (String.ofList (List.replicate 3000000 'x'))\n"
        + PROOF
    )
    assert result.status is ProofStatus.UNPROVED, result.diagnostic
    assert len(result.raw["stdout"]) + len(result.raw["stderr"]) <= 2 * 1024 * 1024


def test_timeout_is_unproved_and_cleanup_is_known():
    result = run(TARGET + "#eval IO.sleep 200000\n" + PROOF, seconds=5)
    assert result.status is ProofStatus.UNPROVED, result.diagnostic
    assert "timeout" in result.diagnostic
    assert b"Building Solution" in result.raw["stdout"]
    assert result.identity["limits"]["seconds"] == 5
    assert result.elapsed_ns < 30 * 10**9
