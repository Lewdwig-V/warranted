"""Refuse a submission that repeats earlier rejected candidates too closely.

Adapted from ReSchema's near-duplicate resubmission guard ("flail guard",
`reschema/engine.py`): small agents resubmit near-identical rejected candidates in
a loop, each costing a full check. Candidates are compared after a domain-supplied
normalisation, against the run's recent candidates that a check rejected. Two
bands, because text alone cannot tell verbatim churn from a legitimate minimal
repair:

- exact repeats (normalised edit mass 0) carry no new information and are refused
  once `exact_repeats` earlier rejections match;
- near repeats (edit mass within the threshold) may be the correct small fix, so
  they are refused only once `near_repeats` earlier rejections match, exact or near.

The core has no default thresholds; a task that wants the guard sets all of them.
"""

from __future__ import annotations

import difflib
from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class DuplicateGuard:
    exact_repeats: int  # refuse once this many earlier rejections match exactly
    near_repeats: int  # refuse once this many match exactly or nearly
    near_edit_floor: int  # near: edit mass at most this many bytes ...
    near_edit_percent: int  # ... or this percentage of the candidate's length
    window: int  # how many recent rejected candidates are compared

    def __post_init__(self):
        for name in ("exact_repeats", "near_repeats", "window"):
            value = getattr(self, name)
            if type(value) is not int or value < 1:
                raise ValueError(f"duplicate guard {name} must be a positive integer")
        for name in ("near_edit_floor", "near_edit_percent"):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ValueError(
                    f"duplicate guard {name} must be a non-negative integer"
                )
        if self.near_edit_percent > 100:
            raise ValueError("duplicate guard near_edit_percent is at most 100")

    def record(self) -> dict[str, int]:
        return {
            "exact_repeats": self.exact_repeats,
            "near_repeats": self.near_repeats,
            "near_edit_floor": self.near_edit_floor,
            "near_edit_percent": self.near_edit_percent,
            "window": self.window,
        }

    def verdict(self, rejected: Sequence[bytes], candidate: bytes) -> dict | None:
        """Why `candidate` is refused, or None. `rejected` is oldest first."""
        threshold = max(
            self.near_edit_floor, len(candidate) * self.near_edit_percent // 100
        )
        exact, near = 0, []
        for earlier in rejected[-self.window :]:
            mass = edit_mass(earlier, candidate)
            if mass == 0:
                exact += 1
            elif mass <= threshold:
                near.append(mass)
        if exact >= self.exact_repeats:
            return {"band": "exact", "matches": exact + len(near), "edit": 0}
        if exact + len(near) >= self.near_repeats:
            return {"band": "near", "matches": exact + len(near), "edit": min(near)}
        return None


def edit_mass(a: bytes, b: bytes) -> int:
    """Bytes added, removed, or replaced between two normalised candidates."""
    matcher = difflib.SequenceMatcher(None, a, b, autojunk=False)
    return sum(
        max(i2 - i1, j2 - j1)
        for tag, i1, i2, j1, j2 in matcher.get_opcodes()
        if tag != "equal"
    )


def default_normalize(candidate) -> bytes:
    """Every captured file, in name order, unchanged."""
    return b"".join(
        name.encode() + b"\0" + data + b"\0" for name, data in sorted(candidate.items())
    )
