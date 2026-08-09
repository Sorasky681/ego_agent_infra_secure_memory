"""A strict, deterministic research lifecycle state machine."""

from typing import Dict, FrozenSet, List, Optional

from .errors import ConflictError
from .models import Stage


ALLOWED_TRANSITIONS: Dict[Stage, FrozenSet[Stage]] = {
    Stage.INTAKE: frozenset({Stage.CONTEXT}),
    Stage.CONTEXT: frozenset({Stage.PLAN}),
    Stage.PLAN: frozenset({Stage.PLAN_REVIEW}),
    Stage.PLAN_REVIEW: frozenset({Stage.PLAN, Stage.APPROVAL}),
    Stage.APPROVAL: frozenset({Stage.EXECUTE, Stage.PLAN}),
    Stage.EXECUTE: frozenset({Stage.OBSERVE}),
    Stage.OBSERVE: frozenset({Stage.EVALUATE}),
    Stage.EVALUATE: frozenset({Stage.VERIFY}),
    Stage.VERIFY: frozenset({Stage.PLAN, Stage.DECIDE}),
    Stage.DECIDE: frozenset({Stage.ARCHIVE}),
    Stage.ARCHIVE: frozenset({Stage.MEMORY_SKILL}),
    Stage.MEMORY_SKILL: frozenset({Stage.COMPLETED}),
    Stage.COMPLETED: frozenset(),
}

FORWARD_PATH: List[Stage] = [
    Stage.INTAKE,
    Stage.CONTEXT,
    Stage.PLAN,
    Stage.PLAN_REVIEW,
    Stage.APPROVAL,
    Stage.EXECUTE,
    Stage.OBSERVE,
    Stage.EVALUATE,
    Stage.VERIFY,
    Stage.DECIDE,
    Stage.ARCHIVE,
    Stage.MEMORY_SKILL,
    Stage.COMPLETED,
]


def next_forward_stage(current: Stage) -> Optional[Stage]:
    try:
        index = FORWARD_PATH.index(current)
    except ValueError:
        return None
    if index == len(FORWARD_PATH) - 1:
        return None
    return FORWARD_PATH[index + 1]


def validate_transition(current: Stage, target: Stage) -> Stage:
    if target not in ALLOWED_TRANSITIONS[current]:
        raise ConflictError(
            "illegal_transition",
            "Illegal research transition: %s -> %s" % (current.value, target.value),
            {
                "current": current.value,
                "target": target.value,
                "allowed": sorted(stage.value for stage in ALLOWED_TRANSITIONS[current]),
            },
        )
    return target


def progress_for(stage: Stage) -> Dict[str, int]:
    index = FORWARD_PATH.index(stage)
    denominator = len(FORWARD_PATH) - 1
    return {
        "step": index + 1,
        "total": len(FORWARD_PATH),
        "percent": round(index * 100 / denominator),
    }
