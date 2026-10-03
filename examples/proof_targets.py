"""The fixtures' proof targets: domain-owned Lean challenges and required theorems.

Shared by the M4 and M5 drivers and their tests; the task-layer domains in
examples/m7 declare the same files in their own checkers.
"""

from pathlib import Path

from warranted import ProofTarget

EXAMPLES = Path(__file__).resolve().parent
TARGETS = {
    "uniqueness": ProofTarget(
        EXAMPLES / "m7/csv/UniquenessChallenge.lean", "Warranted.uniqueness_preserved"
    ),
    "timestamp": ProofTarget(
        EXAMPLES / "m7/csv/TimestampChallenge.lean", "Warranted.timestamp_roundtrip"
    ),
    "migration": ProofTarget(
        EXAMPLES / "m7/migration/MigrationChallenge.lean",
        "Warranted.migration_renaming",
    ),
}
