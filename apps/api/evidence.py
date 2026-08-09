"""Evidence completeness and independence gate."""

from typing import Iterable, List, Set

from .models import EvidenceKind, EvidenceRecord, GateResult, GateStatus
from .provenance import canonical_sha256


REQUIRED_FOR_DECISION: Set[EvidenceKind] = {
    EvidenceKind.CODE,
    EvidenceKind.CONFIG,
    EvidenceKind.DATASET_MANIFEST,
    EvidenceKind.LOG,
    EvidenceKind.METRIC,
    EvidenceKind.TRACE,
    EvidenceKind.REVIEW,
}


def evidence_gate(evidence: Iterable[EvidenceRecord]) -> GateResult:
    records: List[EvidenceRecord] = list(evidence)
    present = {record.kind for record in records}
    missing = REQUIRED_FOR_DECISION - present
    reasons: List[str] = []
    if missing:
        reasons.append(
            "missing required evidence: %s" % ", ".join(sorted(kind.value for kind in missing))
        )

    corrupt = [
        record.id
        for record in records
        if canonical_sha256(record.payload) != record.artifact_digest
    ]
    if corrupt:
        reasons.append("artifact digest mismatch: %s" % ", ".join(sorted(corrupt)))

    metric_records = [record for record in records if record.kind == EvidenceKind.METRIC]
    if metric_records and not any(
        record.payload.get("deterministic") is True
        and record.payload.get("summary_only") is not True
        and (record.payload.get("raw_samples") or record.payload.get("raw_metric_digest"))
        for record in metric_records
    ):
        reasons.append("metric evidence must reference raw values and deterministic evaluation")

    producers = {
        record.producer_id
        for record in records
        if record.kind != EvidenceKind.REVIEW
    }
    independent_reviewer = None
    review_records = [record for record in records if record.kind == EvidenceKind.REVIEW]
    valid_reviews = []
    for review in review_records:
        reviewer_id = review.producer_id
        claimed_reviewer_id = review.payload.get("reviewer_id")
        reviewed_producers = set(review.payload.get("reviewed_producers", []))
        independent = (
            claimed_reviewer_id == reviewer_id
            and review.payload.get("independent") is True
            and review.payload.get("verdict") == "PASS"
            and reviewer_id not in producers
            and bool(reviewed_producers)
            and producers.issubset(reviewed_producers)
        )
        if independent:
            valid_reviews.append(review)
            independent_reviewer = reviewer_id
    if review_records and not valid_reviews:
        reasons.append("an independent PASS review by a non-producing agent is required")

    status = GateStatus.FAIL if reasons else GateStatus.PASS
    return GateResult(
        status=status,
        present=sorted(present, key=lambda kind: kind.value),
        missing=sorted(missing, key=lambda kind: kind.value),
        reasons=reasons,
        independent_reviewer=independent_reviewer,
    )
