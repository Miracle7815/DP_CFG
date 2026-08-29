import random
import time

from ..config import logger
from . import model
from .model import Model


class LocalModel(Model):
    def __init__(self, model_name, time_out=100, parallel_tool_call=False):
        super().__init__(model_name, time_out, parallel_tool_call)
        self.model_path = None
        self.device = None
        self.model = None
        self.tokenizer = None

    def setup(self, model_path, device=None):
        from transformers import AutoModelForCausalLM, AutoTokenizer

        self.model_path = model_path
        self.tokenizer = AutoTokenizer.from_pretrained(model_path)
        self.model = AutoModelForCausalLM.from_pretrained(
            model_path,
            device_map="auto",
            torch_dtype="auto",
            trust_remote_code=True,
        )
        self.device = device

    def call(self, messages, retry=3, temperature=0.0, max_new_tokens=4096, **_) -> model.ModelResponse:
        if self.model is None or self.tokenizer is None:
            raise RuntimeError(f"Local model {self.model_name} is not configured")

        prompt = self.tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        model_inputs = self.tokenizer([prompt], return_tensors="pt").to(self.model.device)

        for try_num in range(retry):
            try:
                generated = self.model.generate(
                    **model_inputs,
                    max_new_tokens=max_new_tokens,
                    temperature=temperature,
                    pad_token_id=self.tokenizer.eos_token_id,
                    eos_token_id=self.tokenizer.eos_token_id,
                )
                completion_ids = [
                    output_ids[len(input_ids):]
                    for input_ids, output_ids in zip(model_inputs.input_ids, generated)
                ]
                content = self.tokenizer.batch_decode(completion_ids, skip_special_tokens=True)[0]
                input_tokens = int(model_inputs.input_ids.size(1))
                output_tokens = len(completion_ids[0])
                model._ensure_thread_cost()
                model.thread_cost.process_input_tokens += input_tokens
                model.thread_cost.process_output_tokens += output_tokens
                return model.ModelResponse(
                    content=content,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    finish_reason="stop",
                )
            except Exception as exc:
                logger.debug(f"Invoking local LLM failed: {exc}")
                if try_num + 1 >= retry:
                    break
                time.sleep(2 * (2 ** try_num) + random.uniform(0, 1))

        raise RuntimeError(f"Invoking local model {self.model_name} failed after {retry} attempts")
