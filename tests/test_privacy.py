from types import SimpleNamespace

import pytest

from generate_for_buggy.privacy import PromptPrivacyError, validate_and_record_prompt


def test_prompt_guard_rejects_raw_cfg_statement_field():
    with pytest.raises(PromptPrivacyError):
        validate_and_record_prompt(
            [{"role": "user", "content": '{"statement": "if (secret > 4)"}'}],
            "test",
        )


def test_prompt_guard_rejects_target_method_body():
    target = SimpleNamespace(content="public int f(int x) { return x + hiddenThreshold; }")
    with pytest.raises(PromptPrivacyError):
        validate_and_record_prompt(
            [{"role": "user", "content": "return x + hiddenThreshold;"}],
            "requirement",
            target,
        )


def test_prompt_guard_accepts_source_free_contract():
    target = SimpleNamespace(content="public int f(int x) { return x + hiddenThreshold; }")
    validate_and_record_prompt(
        [{"role": "user", "content": "Signature: public int f(int x). Returns a documented result."}],
        "requirement",
        target,
    )

