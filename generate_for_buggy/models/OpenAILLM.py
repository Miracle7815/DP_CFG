import random
import time

from openai import OpenAI

from ..config import logger
from . import model


class OpenaiModel(model.Model):
    def __init__(
        self,
        model_name: str,
        time_out: int = 100,
        parallel_tool_call: bool = False,
    ):
        super().__init__(model_name, time_out, parallel_tool_call)
        self.client = None

    def setup(self, api_key: str, base_url: str | None) -> None:
        if not api_key:
            raise ValueError(f"Missing API key for model {self.model_name}")
        self.api_key = api_key
        self.base_url = base_url
        self.client = OpenAI(api_key=api_key, base_url=base_url, timeout=self.timeout)

    def call(self, messages, tools=None, retry=3, temperature=0.0) -> model.ModelResponse:
        if self.client is None:
            raise RuntimeError(f"Model {self.model_name} is not configured")

        base_delay = 2
        for try_num in range(retry):
            try:
                request = {
                    "model": self.model_name,
                    "messages": messages,
                    "temperature": temperature,
                    "stream": False,
                }
                if tools is not None:
                    request.update({"tools": tools, "tool_choice": "auto"})
                response = self.client.chat.completions.create(**request)

                usage = response.usage
                input_tokens = int(usage.prompt_tokens or 0)
                output_tokens = int(usage.completion_tokens or 0)
                model._ensure_thread_cost()
                model.thread_cost.process_input_tokens += input_tokens
                model.thread_cost.process_output_tokens += output_tokens

                message = response.choices[0].message
                return model.ModelResponse(
                    content=message.content or "",
                    tool_calls=list(message.tool_calls or []),
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    finish_reason=response.choices[0].finish_reason,
                )
            except Exception as exc:
                logger.debug(f"Invoking LLM failed: {exc}")
                if try_num + 1 >= retry:
                    break
                time.sleep(base_delay * (2 ** try_num) + random.uniform(0, 1))

        raise RuntimeError(f"Invoking model {self.model_name} failed after {retry} attempts")
