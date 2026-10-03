"""The token-bound measurement script, against an offline fake model server."""

import json
import runpy
from pathlib import Path

import pytest
from test_chat_completions import response, server

SCRIPT = Path(__file__).resolve().parents[1] / "examples/m7/token_bound.py"
DIGEST = "c" * 64
INSTALLED = {"model": {"name": "gemma4:26b", "digest": DIGEST}}


def run(tmp_path, body, metadata=INSTALLED):
    main = runpy.run_path(str(SCRIPT))["main"]
    with server(body, metadata=metadata) as (url, calls):
        code = main(
            [
                "measure",
                str(tmp_path / "out"),
                "--base-url",
                url,
                "--model",
                "gemma4:26b",
            ]
        )
    return code, calls


def test_a_model_within_the_bound_reports_its_digest_pinned_key(tmp_path, capsys):
    code, calls = run(tmp_path, response())
    report = json.loads((tmp_path / "out/report.json").read_bytes())
    assert code == 0 and report["passed"]
    assert report["verification_key"] == f"gemma4:26b@{DIGEST}"
    assert f"gemma4:26b@{DIGEST}" in capsys.readouterr().out
    posts = [body for path, body in calls if path == "/v1/chat/completions"]
    assert len(posts) == len(report["cases"])
    for row in report["cases"]:
        wire = (tmp_path / "out/raw" / f"{row['case']}.request.json").read_bytes()
        assert row["wire_bytes"] == len(wire)
        assert row["bound"] >= len(wire)


def test_a_case_above_the_bound_fails_the_measurement(tmp_path):
    over = {"prompt_tokens": 10**6, "completion_tokens": 1, "total_tokens": 10**6 + 1}
    code, _ = run(tmp_path, response(usage=over))
    report = json.loads((tmp_path / "out/report.json").read_bytes())
    assert code == 1 and not report["passed"]


def test_missing_usage_is_unmeasured_not_a_pass(tmp_path):
    code, _ = run(tmp_path, response(usage=None))
    assert code == 1


def test_a_tag_that_is_not_installed_is_refused_before_inference(tmp_path):
    other = {"model": {"name": "other:1b", "digest": DIGEST}}
    with pytest.raises(ValueError, match="not installed"):
        run(tmp_path, response(), other)
