from openai import OpenAI
from model import Model
import sys
from ..config import logger

class OpenaiModel(Model):
    _instances = {}
    
    def __new__(cls):         
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

    def 