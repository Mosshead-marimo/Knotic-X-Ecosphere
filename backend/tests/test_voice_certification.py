from datetime import UTC, datetime

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from knotic_api.voice.certification import (
    MatrixResult,
    VoiceCertificationEvidence,
    VoiceReleasePolicy,
    evaluate,
    sign_report,
    verify_report,
)


def evidence(*, approved: bool = True, matrix_passed: bool = True) -> VoiceCertificationEvidence:
    browsers = ("Chrome", "Edge", "Firefox", "Safari")
    operating_systems = ("Windows", "macOS", "iOS", "Android")
    devices = ("built-in", "wired", "Bluetooth")
    networks = ("broadband", "4G", "impaired", "offline-restored")
    matrix = [
        MatrixResult(
            browser=browsers[index % len(browsers)],
            browser_version=str(index),
            os=operating_systems[index % len(operating_systems)],
            device=devices[index % len(devices)],
            network=networks[index % len(networks)],
            passed=matrix_passed,
            evidence_uri=f"artifact://matrix/{index}",
        )
        for index in range(20)
    ]
    return VoiceCertificationEvidence(
        environment="production-like",
        commit_sha="a" * 40,
        measured_at=datetime.now(UTC),
        first_audio_ms=[500, 700, 800, 1_000, 1_400] * 200,
        interruption_ms=[80, 100, 110, 180, 250] * 200,
        total_turns=1_000,
        failed_turns=5,
        peak_concurrent_calls=100,
        soak_minutes=120,
        quota_peak_percent=70,
        matrix=matrix,
        provider_approval_reference="approval://voice/2026-09" if approved else None,
    )


def test_approved_evidence_passes_every_release_threshold() -> None:
    report = evaluate(evidence(), VoiceReleasePolicy(), evaluated_at=datetime.now(UTC))
    assert report.status == "PASS"
    assert report.violations == ()
    assert report.first_audio_ms.p95 == 1_400
    assert report.interruption_ms.p99 == 250


def test_missing_provider_approval_never_produces_a_pass() -> None:
    report = evaluate(evidence(approved=False), VoiceReleasePolicy())
    assert report.status == "INCOMPLETE"
    assert "PROVIDER_APPROVAL_MISSING" in report.violations


def test_matrix_failure_blocks_release() -> None:
    report = evaluate(evidence(matrix_passed=False), VoiceReleasePolicy())
    assert report.status == "FAIL"
    assert "MATRIX_FAILURE" in report.violations


def test_report_signature_detects_any_post_signing_change() -> None:
    private_key = Ed25519PrivateKey.generate()
    signed = sign_report(evaluate(evidence(), VoiceReleasePolicy()), private_key, key_id="release-test")
    verify_report(signed, private_key.public_key())
    tampered = signed.model_copy(update={"report": signed.report.model_copy(update={"commit_sha": "b" * 40})})
    with pytest.raises(InvalidSignature):
        verify_report(tampered, private_key.public_key())
