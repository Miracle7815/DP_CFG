from openai import OpenAI
import sys
from ..config import logger
from tenacity import retry, stop_after_attempt, wait_random_exponential
from . import model
import random
import time

class OpenaiModel(model.Model):
    _instances = {}
    
    def __new__(cls , *args):         
        if cls not in cls._instances:
            cls._instances[cls] = super().__new__(cls)
            cls._instances[cls]._initialized = False
        return cls._instances[cls]

    def __init__(
            self,
            model_name: str,
            time_out: int = 100,
            parallel_tool_call: bool = False,
        ):
            
        super().__init__(model_name , time_out , parallel_tool_call)

        self.api_key = None
        self.base_url = None
        
        self.client = None

    def setup(self , api_key , base_url):
        if api_key is not None:
            self.api_key = api_key
        else:
            logger.debug("Please set valid api key !!")
            sys.exit(1)
        
        self.base_url = base_url

        self.client = OpenAI(
            api_key = api_key,
            base_url = base_url,
            timeout = self.timeout
        )


    def call(self , messages , retry=3 , temperature = 0.0):
        if self.client is None:
            return None
        
        base_delay = 2

        for try_num in range(retry):
            try:
                response = self.client.chat.completions.create(
                    model=self.model_name,
                    messages=messages,
                    temperature=temperature,
                    stream=False    
                )

                usage_stats = response.usage

                input_tokens = int(usage_stats.prompt_tokens)
                output_tokens = int(usage_stats.completion_tokens)

                model.thread_cost.process_input_tokens += input_tokens
                model.thread_cost.process_output_tokens += output_tokens

                response_content = response.choices[0].message.content
                if response_content is None:
                    response_content = ""

                reason = response.choices[0].finish_reason

                return response_content , input_tokens , output_tokens , reason
            except Exception as e:
                logger.debug(f"Invoking LLM error !!!\nmessage:\n{e}")
                delay = base_delay * (2 ** try_num) + random.uniform(0, 1)  # 退避策略
                logger.debug(f"Retrying in {delay:.2f} seconds... "
                      f"(Attempt {try_num + 1}/{retry})")
                time.sleep(delay)
                
        raise RuntimeError("Invoking LLM error !!!")