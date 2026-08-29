import pytest

from generate_for_buggy.models import model


class SetupModel(model.Model):
    def __init__(self, name, base_url=None):
        super().__init__(name)
        self.base_url = base_url
        self.client = None

    def setup(self, api_key, base_url):
        self.api_key = api_key
        self.base_url = base_url
        self.client = object()

    def call(self, messages, **kwargs):
        return model.ModelResponse(content="ok")


def test_role_models_keep_independent_instances():
    previous = (model.SELECTED_MODEL, model.REVIEW_MODEL, list(model.ATTRIBUTION_MODELS))
    try:
        selected = SetupModel("selected")
        review = SetupModel("review")
        voters = [SetupModel(f"voter-{index}", f"https://v{index}.invalid") for index in range(3)]
        model.set_model(selected, "selected-key", "https://selected.invalid")
        model.set_review_model(review, "review-key", "https://review.invalid")
        model.set_attribution_models(voters)
        assert model.SELECTED_MODEL is selected
        assert model.REVIEW_MODEL is review
        assert len({id(item) for item in model.ATTRIBUTION_MODELS}) == 3
        assert selected.api_key != review.api_key
    finally:
        model.SELECTED_MODEL, model.REVIEW_MODEL, model.ATTRIBUTION_MODELS = previous


def test_duplicate_attribution_config_is_rejected():
    voters = [SetupModel("same", "https://same.invalid") for _ in range(3)]
    with pytest.raises(ValueError):
        model.set_attribution_models(voters)

