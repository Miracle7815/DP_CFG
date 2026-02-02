from model import Model
import model
from transformers import AutoTokenizer , AutoModelForCausalLM
import os
from ..config import logger
import random
import time

os.environ['HF_ENDPOINT'] = 'https://hf-mirror.com'

class LocalModel(Model):
    _instances = {}

    def __new__(cls):         
        if cls not in cls._instances:
            cls._instances[cls] = super().__new__(cls)
            cls._instances[cls]._initialized = False
        return cls._instances[cls]
    
    def __init__(self, model_name, time_out = 100, parallel_tool_call = False):
        super().__init__(model_name , time_out , parallel_tool_call)

        self.model_path = None
        self.device = None
        
        self.model = None

        self.tokenizer = None
    
    def setup(self , model_path , device):
        self.model_path = model_path

        self.tokenizer = AutoTokenizer.from_pretrained(self.model_name)
        self.model = AutoModelForCausalLM.from_pretrained(
            self.model_path,
            device_map="auto",
            torch_dtype="auto",
            trust_remote_code = True
        )

        self.device = device

        # load local_model

    def call(self , messages , retry=3 , temperature = 0.0 , max_new_tokens=4096):
        if self.model is None or self.tokenizer is None:
            return None
        
        prompt = self.tokenizer.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=True
        )

        model_inputs = self.tokenizer([prompt] , return_tensors="pt").to(self.model.device)

        base_delay = 2

        for try_num in range(retry):
            try:
                generated_ids = self.model.generate(
                    **model_inputs,
                    max_new_tokens=max_new_tokens,
                    temperature=temperature,
                    pad_token_id=self.tokenizer.eos_token_id,
                    eos_token_id=self.tokenizer.eos_token_id,
                )

                generated_ids = [output_ids[len(input_ids):] for input_ids, output_ids in zip(model_inputs.input_ids, generated_ids)]
                response = self.tokenizer.batch_decode(generated_ids, skip_special_tokens=True)[0]
            
                model.thread_cost.thread_cost.process_input_tokens += model_inputs.size(1)
                model.thread_cost.process_output_tokens += len(generated_ids[0])

            except Exception as e:
                logger.debug(f"Invoking LLM error !!!\nmessage:\n{e}")
                delay = base_delay * (2 ** try_num) + random.uniform(0, 1)  # 退避策略
                logger.debug(f"Retrying in {delay:.2f} seconds... "
                      f"(Attempt {try_num + 1}/{retry})")
                time.sleep(delay)
            