from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from semifinal_acceptance import AcceptanceError, build_bundle, verify_bundle
from tests.acceptance.conftest import build_acceptance_source


def test_build_is_deterministic_and_offline_replayable(
    acceptance_source: Path, tmp_path: Path
) -> None:
    first = tmp_path / "bundle-a"
    second = tmp_path / "bundle-b"
    built_a = build_bundle(acceptance_source, first)
    built_b = build_bundle(acceptance_source, second)
    assert built_a["bundle_root"] == built_b["bundle_root"]
    assert (first / "manifest.json").read_bytes() == (second / "manifest.json").read_bytes()
    verified = verify_bundle(first)
    assert verified["status"] == "PASS"
    assert verified["mvp_coverage"] == "8/14"
    assert verified["full_release_status"] == "NOT_EVALUATED"
    assert verified["external_calls"] == 0


def test_artifact_tamper_breaks_bundle_replay(
    acceptance_source: Path, tmp_path: Path
) -> None:
    bundle = tmp_path / "bundle"
    build_bundle(acceptance_source, bundle)
    target = bundle / "artifacts/metrics/raw.jsonl"
    target.write_bytes(target.read_bytes() + b" ")
    with pytest.raises(AcceptanceError, match="artifact digest mismatch"):
        verify_bundle(bundle)


def test_secret_is_rejected_before_bundle_creation(tmp_path: Path) -> None:
    source = build_acceptance_source(tmp_path / "source")
    (source / "leaked.json").write_text(
        '{"approval_token":"this-is-a-live-secret-value"}\n', encoding="utf-8"
    )
    with pytest.raises(AcceptanceError, match="possible secret JSON field"):
        build_bundle(source, tmp_path / "bundle")


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "nan", "filter", "summary"])
def test_raw_evidence_failures_are_fail_closed(tmp_path: Path, mutation: str) -> None:
    source = build_acceptance_source(tmp_path / mutation / "source")
    raw = source / "metrics/raw.jsonl"
    lines = raw.read_text(encoding="utf-8").splitlines()
    if mutation == "missing":
        raw.write_text(lines[0] + "\n", encoding="utf-8")
    elif mutation == "duplicate":
        duplicate = json.loads(lines[0])
        duplicate["record_id"] = "r3"
        raw.write_text("\n".join(lines + [json.dumps(duplicate)]) + "\n", encoding="utf-8")
    elif mutation == "nan":
        gpu = source / "runtime/gpu-metrics.jsonl"
        gpu.write_text(gpu.read_text(encoding="utf-8").replace('"power_w":100', '"power_w":NaN'), encoding="utf-8")
    elif mutation == "filter":
        record = json.loads(lines[0])
        record["included"] = False
        record["filter_id"] = "post-hoc-filter"
        raw.write_text(json.dumps(record) + "\n" + lines[1] + "\n", encoding="utf-8")
    else:
        summary = source / "metrics/summary.json"
        value = json.loads(summary.read_text(encoding="utf-8"))
        value["sum_scaled"] = 999
        summary.write_text(json.dumps(value) + "\n", encoding="utf-8")
    with pytest.raises(AcceptanceError):
        build_bundle(source, tmp_path / mutation / "bundle")


def test_mvp_cannot_promote_itself_to_full_release(tmp_path: Path) -> None:
    source = build_acceptance_source(tmp_path / "source")
    descriptor_path = source / "acceptance-input.json"
    descriptor = json.loads(descriptor_path.read_text(encoding="utf-8"))
    descriptor["corpus"]["full_release_status"] = "PASS"
    descriptor_path.write_text(json.dumps(descriptor) + "\n", encoding="utf-8")
    with pytest.raises(AcceptanceError, match="corpus/gate declaration mismatch"):
        build_bundle(source, tmp_path / "bundle")


def test_cli_build_and_verify(acceptance_source: Path, tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    build = subprocess.run(
        [
            sys.executable,
            "-m",
            "semifinal_acceptance",
            "build",
            "--source",
            str(acceptance_source),
            "--output",
            str(bundle),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert build.returncode == 0, build.stderr
    verify = subprocess.run(
        [
            sys.executable,
            "-m",
            "semifinal_acceptance",
            "verify",
            "--bundle",
            str(bundle),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert verify.returncode == 0, verify.stderr
    assert json.loads(verify.stdout)["full_release_status"] == "NOT_EVALUATED"


def test_undeclared_bundle_file_is_rejected(acceptance_source: Path, tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    build_bundle(acceptance_source, bundle)
    extra = bundle / "artifacts/extra.txt"
    extra.write_text("not in the manifest", encoding="utf-8")
    with pytest.raises(AcceptanceError, match="undeclared artifacts"):
        verify_bundle(bundle)
