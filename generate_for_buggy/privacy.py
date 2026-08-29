from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Optional

from .config import CONFIG


class PromptPrivacyError(ValueError):
    pass


_PROMPT_AUDIT: list[dict] = []
_FORBIDDEN_SERIALIZED_KEYS = (
    '"statement":',
    '"source_code":',
    '"line_number":',
    '"private_method":',
    '"witness_value":',
    '"actual_buggy_output":',
)


def audit_checkpoint() -> int:
    return len(_PROMPT_AUDIT)


def audit_summary_since(checkpoint: int) -> list[dict]:
    return [dict(item) for item in _PROMPT_AUDIT[checkpoint:]]


def validate_and_record_prompt(
    messages: list[dict],
    stage: str,
    target_method: Optional[object] = None,
) -> None:
    serialized = json.dumps(messages, ensure_ascii=False, sort_keys=True)
    lowered = serialized.lower()
    unescaped_lowered = lowered.replace('\\"', '"')
    for forbidden in _FORBIDDEN_SERIALIZED_KEYS:
        if forbidden in lowered or forbidden in unescaped_lowered:
            raise PromptPrivacyError(f"Prompt contains forbidden private field {forbidden}")

    if re.search(r"(?:[A-Za-z]:)?[^\s\"']+\.java(?::|%3A)\d+", serialized):
        raise PromptPrivacyError("Prompt contains a Java source location")

    fixed_root = CONFIG.get("offline_evaluation", {}).get("fixed_loc", "")
    if fixed_root:
        normalised_fixed = os.path.normcase(os.path.abspath(fixed_root))
        escaped_fixed = json.dumps(normalised_fixed, ensure_ascii=False)[1:-1].lower()
        if normalised_fixed.lower() in lowered or escaped_fixed in lowered:
            raise PromptPrivacyError("Prompt contains the fixed checkout path")

    if target_method is not None:
        source = getattr(target_method, "content", "") or ""
        body_start = source.find("{")
        body_end = source.rfind("}")
        body = source[body_start + 1:body_end].strip() if 0 <= body_start < body_end else ""
        if len(body) >= 8 and body in serialized:
            raise PromptPrivacyError("Prompt contains the target method body")
        for line in body.splitlines():
            fragment = line.strip()
            if len(fragment) >= 16 and fragment not in {"{", "}"} and fragment in serialized:
                raise PromptPrivacyError("Prompt contains a target source fragment")

    _PROMPT_AUDIT.append({
        "stage": stage,
        "sha256": hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
        "privacy_check": "passed",
    })
