"""Validated research-memory ranking and write eligibility."""

from .errors import PolicyError
from .models import GateResult, GateStatus, MemorySignals


MEMORY_WEIGHTS = {
    "semantic": 0.45,
    "component": 0.20,
    "evidence": 0.15,
    "recency": 0.10,
    "failure": 0.10,
}


def score_memory(signals: MemorySignals) -> float:
    score = sum(getattr(signals, signal) * weight for signal, weight in MEMORY_WEIGHTS.items())
    return round(score, 6)


def require_validated_memory(gate: GateResult, review_id: str) -> None:
    if gate.status != GateStatus.PASS:
        raise PolicyError(
            "memory_not_validated",
            "Research memory can only be written after the evidence gate passes",
            {"gate_status": gate.status.value},
        )
    if not review_id or gate.independent_reviewer != review_id:
        raise PolicyError(
            "memory_review_mismatch",
            "Memory must cite the independent review that passed the evidence gate",
            {
                "review_id": review_id,
                "independent_reviewer": gate.independent_reviewer,
            },
        )
