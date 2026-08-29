import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional


thread_cost = threading.local()


def _ensure_thread_cost() -> None:
    if not hasattr(thread_cost, "process_input_tokens"):
        thread_cost.process_input_tokens = 0
    if not hasattr(thread_cost, "process_output_tokens"):
        thread_cost.process_output_tokens = 0


@dataclass(frozen=True)
class ModelResponse:
    content: str = ""
    tool_calls: list[Any] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    finish_reason: Optional[str] = None


class Model(ABC):
    def __init__(
        self,
        model_name: str,
        time_out: int = 100,
        parallel_tool_call: bool = False,
    ):
        self.model_name = model_name
        self.timeout = time_out
        self.parallel_tool_call = parallel_tool_call
        self.api_key = None
        self.base_url = None

    @abstractmethod
    def call(self, messages: list[dict], **kwargs) -> ModelResponse:
        raise NotImplementedError

    def get_overall_exec_stats(self) -> dict:
        _ensure_thread_cost()
        return {
            "model": self.model_name,
            "total_input_tokens": thread_cost.process_input_tokens,
            "total_output_tokens": thread_cost.process_output_tokens,
            "total_tokens": thread_cost.process_input_tokens + thread_cost.process_output_tokens,
        }


SELECTED_MODEL: Optional[Model] = None
REVIEW_MODEL: Optional[Model] = None
ATTRIBUTION_MODELS: list[Model] = []


def _setup(model_instance: Model, api_key: str, base_url: Optional[str]) -> Model:
    if (
        model_instance.api_key == api_key
        and model_instance.base_url == base_url
        and getattr(model_instance, "client", None) is not None
    ):
        return model_instance
    setup = getattr(model_instance, "setup", None)
    if setup is not None:
        setup(api_key, base_url)
    return model_instance


def set_model(model_instance: Model, api_key: str, base_url: Optional[str]) -> None:
    global SELECTED_MODEL
    SELECTED_MODEL = _setup(model_instance, api_key, base_url)


def set_review_model(model_instance: Model, api_key: str, base_url: Optional[str]) -> None:
    global REVIEW_MODEL
    REVIEW_MODEL = _setup(model_instance, api_key, base_url)


def set_attribution_models(models: list[Model]) -> None:
    global ATTRIBUTION_MODELS
    identities = [(item.__class__.__name__, item.model_name, item.base_url) for item in models]
    if len(models) != 3 or len(set(identities)) != 3:
        raise ValueError("Exactly three distinct attribution model configurations are required")
    ATTRIBUTION_MODELS = list(models)


def require_model(value: Optional[Model], role: str) -> Model:
    if value is None:
        raise RuntimeError(f"The {role} model is not configured")
    return value
