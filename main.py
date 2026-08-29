import os

from generate_for_buggy.config import CONFIG
from generate_for_buggy.models.OpenAILLM import OpenaiModel
from generate_for_buggy.models.model import set_attribution_models, set_model, set_review_model
from generate_for_buggy.run_generate import generate_entry


def _model_from_environment(spec: dict, role: str) -> OpenaiModel:
    model_name = os.environ.get(spec["model_env"], "").strip()
    api_key = os.environ.get(spec["api_key_env"], "").strip()
    base_url = os.environ.get(spec["base_url_env"], "").strip() or None
    if not model_name or not api_key:
        raise RuntimeError(
            f"Missing {role} model configuration. Set {spec['model_env']} and {spec['api_key_env']}."
        )
    instance = OpenaiModel(model_name)
    instance.setup(api_key, base_url)
    return instance


def configure_models() -> None:
    specs = CONFIG["models"]
    selected = _model_from_environment(specs["selected"], "selected")
    review = _model_from_environment(specs["review"], "review")
    set_model(selected, selected.api_key, selected.base_url)
    set_review_model(review, review.api_key, review.base_url)

    attribution_models = [
        _model_from_environment(spec, f"attribution-{index + 1}")
        for index, spec in enumerate(specs["attribution"])
    ]
    set_attribution_models(attribution_models)


if __name__ == "__main__":
    configure_models()
    generate_entry()
