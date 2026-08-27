from __future__ import annotations


DETERMINISTIC_CLARIFY_POLICY_VERSION = "deterministic-recoverable-slot"
DETERMINISTIC_CLARIFY_POLICY_RULE = (
    "Mask exactly one recoverable slot from the initial user request, "
    "require one clarification question before acting, and provide the slot "
    "value through a deterministic user reply derived from the original public task "
    "or benchmark state. Do not mask a time reference when the benchmark family "
    "already evaluates nearest-fallback temporal resolution from an explicit anchor."
)


def clarify_policy_manifest_fields(*, clarify_count: int, clarify_scope: str) -> dict[str, object]:
    return {
        "clarify_policy_version": DETERMINISTIC_CLARIFY_POLICY_VERSION,
        "clarify_policy_rule": DETERMINISTIC_CLARIFY_POLICY_RULE,
        "clarify_training_rows": clarify_count,
        "clarify_scope": clarify_scope,
    }
