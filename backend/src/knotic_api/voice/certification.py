"""Deterministic realtime voice release certification and signed reports (P4-T010)."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from pydantic import BaseModel, ConfigDict, Field, model_validator


class MatrixResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    browser: str = Field(min_length=1, max_length=40)
    browser_version: str = Field(min_length=1, max_length=40)
    os: str = Field(min_length=1, max_length=40)
    device: str = Field(min_length=1, max_length=80)
    network: str = Field(min_length=1, max_length=40)
    passed: bool
    evidence_uri: str = Field(min_length=1, max_length=500)


class VoiceCertificationEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    environment: Literal["staging", "production-like"]
    commit_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    measured_at: datetime
    first_audio_ms: list[float] = Field(min_length=1)
    interruption_ms: list[float] = Field(min_length=1)
    total_turns: int = Field(ge=1)
    failed_turns: int = Field(ge=0)
    peak_concurrent_calls: int = Field(ge=0)
    soak_minutes: float = Field(ge=0)
    quota_peak_percent: float = Field(ge=0, le=100)
    matrix: list[MatrixResult] = Field(min_length=1)
    provider_approval_reference: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def validate_counts(self) -> VoiceCertificationEvidence:
        if self.failed_turns > self.total_turns:
            raise ValueError("failed_turns cannot exceed total_turns")
        if self.measured_at.tzinfo is None or self.measured_at.utcoffset() is None:
            raise ValueError("measured_at must include a timezone")
        return self


class VoiceReleasePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    policy_version: str = "voice-release-v1"
    minimum_turns: int = Field(default=1_000, ge=100)
    minimum_concurrent_calls: int = Field(default=100, ge=1)
    minimum_soak_minutes: float = Field(default=120, ge=30)
    maximum_error_rate: float = Field(default=0.01, gt=0, le=0.1)
    maximum_quota_peak_percent: float = Field(default=80, gt=0, le=100)
    first_audio_p50_ms: float = Field(default=800, gt=0)
    first_audio_p95_ms: float = Field(default=1_500, gt=0)
    first_audio_p99_ms: float = Field(default=2_500, gt=0)
    interruption_p50_ms: float = Field(default=120, gt=0)
    interruption_p95_ms: float = Field(default=300, gt=0)
    interruption_p99_ms: float = Field(default=500, gt=0)
    required_matrix_cases: int = Field(default=20, ge=1)
    minimum_first_audio_samples: int = Field(default=500, ge=100)
    minimum_interruption_samples: int = Field(default=100, ge=20)
    required_browsers: tuple[str, ...] = ("Chrome", "Edge", "Firefox", "Safari")
    required_operating_systems: tuple[str, ...] = ("Windows", "macOS", "iOS", "Android")
    required_devices: tuple[str, ...] = ("built-in", "wired", "Bluetooth")
    required_networks: tuple[str, ...] = ("broadband", "4G", "impaired", "offline-restored")


class Percentiles(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    p50: float
    p95: float
    p99: float


class VoiceCertificationReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    status: Literal["PASS", "FAIL", "INCOMPLETE"]
    policy_version: str
    environment: str
    commit_sha: str
    measured_at: datetime
    evaluated_at: datetime
    first_audio_ms: Percentiles
    interruption_ms: Percentiles
    error_rate: float
    total_turns: int
    peak_concurrent_calls: int
    soak_minutes: float
    quota_peak_percent: float
    matrix_total: int
    matrix_passed: int
    violations: tuple[str, ...]
    evidence_sha256: str
    provider_approval_reference: str | None

    def canonical_bytes(self) -> bytes:
        return json.dumps(
            self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode()


class SignedVoiceCertificationReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    report: VoiceCertificationReport
    key_id: str = Field(min_length=1, max_length=128)
    signature: str


def percentile(values: list[float], quantile: float) -> float:
    if not values or not 0 < quantile <= 1:
        raise ValueError("percentile requires samples and a quantile in (0,1]")
    ordered = sorted(values)
    return float(ordered[max(0, math.ceil(len(ordered) * quantile) - 1)])


def evaluate(
    evidence: VoiceCertificationEvidence,
    policy: VoiceReleasePolicy,
    *,
    evaluated_at: datetime | None = None,
) -> VoiceCertificationReport:
    first_audio = Percentiles(
        p50=percentile(evidence.first_audio_ms, 0.50),
        p95=percentile(evidence.first_audio_ms, 0.95),
        p99=percentile(evidence.first_audio_ms, 0.99),
    )
    interruption = Percentiles(
        p50=percentile(evidence.interruption_ms, 0.50),
        p95=percentile(evidence.interruption_ms, 0.95),
        p99=percentile(evidence.interruption_ms, 0.99),
    )
    error_rate = evidence.failed_turns / evidence.total_turns
    violations: list[str] = []
    if evidence.provider_approval_reference is None:
        violations.append("PROVIDER_APPROVAL_MISSING")
    checks = {
        "INSUFFICIENT_TURNS": evidence.total_turns >= policy.minimum_turns,
        "CONCURRENCY_BELOW_TARGET": evidence.peak_concurrent_calls >= policy.minimum_concurrent_calls,
        "SOAK_DURATION_BELOW_TARGET": evidence.soak_minutes >= policy.minimum_soak_minutes,
        "ERROR_RATE_HIGH": error_rate <= policy.maximum_error_rate,
        "QUOTA_HEADROOM_LOW": evidence.quota_peak_percent <= policy.maximum_quota_peak_percent,
        "FIRST_AUDIO_P50_HIGH": first_audio.p50 <= policy.first_audio_p50_ms,
        "FIRST_AUDIO_P95_HIGH": first_audio.p95 <= policy.first_audio_p95_ms,
        "FIRST_AUDIO_P99_HIGH": first_audio.p99 <= policy.first_audio_p99_ms,
        "INTERRUPTION_P50_HIGH": interruption.p50 <= policy.interruption_p50_ms,
        "INTERRUPTION_P95_HIGH": interruption.p95 <= policy.interruption_p95_ms,
        "INTERRUPTION_P99_HIGH": interruption.p99 <= policy.interruption_p99_ms,
        "MATRIX_INCOMPLETE": len(evidence.matrix) >= policy.required_matrix_cases,
        "MATRIX_FAILURE": all(item.passed for item in evidence.matrix),
        "FIRST_AUDIO_SAMPLES_INSUFFICIENT": len(evidence.first_audio_ms) >= policy.minimum_first_audio_samples,
        "INTERRUPTION_SAMPLES_INSUFFICIENT": len(evidence.interruption_ms) >= policy.minimum_interruption_samples,
        "BROWSER_COVERAGE_INCOMPLETE": set(policy.required_browsers).issubset(
            {item.browser for item in evidence.matrix}
        ),
        "OS_COVERAGE_INCOMPLETE": set(policy.required_operating_systems).issubset(
            {item.os for item in evidence.matrix}
        ),
        "DEVICE_COVERAGE_INCOMPLETE": set(policy.required_devices).issubset({item.device for item in evidence.matrix}),
        "NETWORK_COVERAGE_INCOMPLETE": set(policy.required_networks).issubset(
            {item.network for item in evidence.matrix}
        ),
    }
    failed_checks = [code for code, passed in checks.items() if not passed]
    violations.extend(failed_checks)
    status: Literal["PASS", "FAIL", "INCOMPLETE"] = (
        "FAIL" if failed_checks else ("INCOMPLETE" if evidence.provider_approval_reference is None else "PASS")
    )
    evidence_bytes = json.dumps(
        evidence.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode()
    return VoiceCertificationReport(
        status=status,
        policy_version=policy.policy_version,
        environment=evidence.environment,
        commit_sha=evidence.commit_sha,
        measured_at=evidence.measured_at.astimezone(UTC),
        evaluated_at=(evaluated_at or datetime.now(UTC)).astimezone(UTC),
        first_audio_ms=first_audio,
        interruption_ms=interruption,
        error_rate=error_rate,
        total_turns=evidence.total_turns,
        peak_concurrent_calls=evidence.peak_concurrent_calls,
        soak_minutes=evidence.soak_minutes,
        quota_peak_percent=evidence.quota_peak_percent,
        matrix_total=len(evidence.matrix),
        matrix_passed=sum(item.passed for item in evidence.matrix),
        violations=tuple(violations),
        evidence_sha256=hashlib.sha256(evidence_bytes).hexdigest(),
        provider_approval_reference=evidence.provider_approval_reference,
    )


def sign_report(
    report: VoiceCertificationReport, private_key: Ed25519PrivateKey, *, key_id: str
) -> SignedVoiceCertificationReport:
    signature = private_key.sign(report.canonical_bytes())
    return SignedVoiceCertificationReport(
        report=report, key_id=key_id, signature=base64.b64encode(signature).decode("ascii")
    )


def verify_report(signed: SignedVoiceCertificationReport, public_key: Ed25519PublicKey) -> None:
    public_key.verify(base64.b64decode(signed.signature, validate=True), signed.report.canonical_bytes())


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate and sign realtime voice release evidence")
    parser.add_argument("evidence", type=Path)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--private-key", type=Path, required=True)
    parser.add_argument("--key-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    evidence = VoiceCertificationEvidence.model_validate_json(args.evidence.read_bytes())
    policy = VoiceReleasePolicy.model_validate_json(args.policy.read_bytes())
    key = serialization.load_pem_private_key(args.private_key.read_bytes(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise TypeError("private key must be Ed25519")
    signed = sign_report(evaluate(evidence, policy), key, key_id=args.key_id)
    args.output.write_text(signed.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return 0 if signed.report.status == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
