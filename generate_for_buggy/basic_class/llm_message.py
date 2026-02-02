import json
from pprint import pformat
from pathlib import Path

class MessageThread:
    """
    Represents a thread of conversation with the model.
    Abstrated into a class so that we can dump this to a file at any point.
    """

    def __init__(self, messages=None):
        self.messages: list[dict] = messages or []

    def add(self, role: str, message: str):
        """
        Add a new message to the thread.
        Args:
            message (str): The content of the new message.
            role (str): The role of the new message.
        """
        self.messages.append({"role": role, "content": message})

    def add_system(self, message: str):
        self.messages.append({"role": "system", "content": message})

    def add_user(self, message: str):
        self.messages.append({"role": "user", "content": message})

    # def add_tool(self, message: str, tool_call_id: str):
    #     m = {"role": "tool", "content": message, "tool_call_id": tool_call_id}
    #     self.messages.append(m)

    def add_tool(self, message: str):
        self.messages.append({"role": "tool", "content": message})

    def clean_thread(self):
        self.messages = []    
    
    def pop_message(self):
        if len(self.messages) != 0:
            message = self.messages[-1]
            self.messages = self.messages[:-1]
            return message

        return None

    def add_model(
        self, message: str | None, tools = []
    ):
        # let's serialize tools into json first
        json_tools = []
        for i , tool in enumerate(tools):
            this_tool_dict = {}
            this_tool_dict["id"] = tool.tool_id
            # this_tool_dict["type"] = tool.type
            # now serialize function as well
            # func_obj = tool.function
            # func_args: str = func_obj.arguments
            # func_name: str = func_obj.name

            func_args: str = tool.arguments
            func_name: str = tool.function_name

            this_tool_dict["function"] = {"name": func_name, "arguments": func_args}
            json_tools.append(this_tool_dict)

        # if json_tools == []:
        #     # there is no tool calls from the model last time,
        #     # the best we could do is to return the generated text
        #     self.messages.append({"role": "assistant", "content": message})
        # else:
        #     self.messages.append(
        #         {"role": "assistant", "content": None, "tool_calls": json_tools}
        #     )

        if json_tools == []:
            # there is no tool calls from the model last time,
            # the best we could do is to return the generated text
            self.messages.append({"role": "assistant", "content": message})
        else:
            self.messages.append(
                {"role": "assistant", "content": json_tools}
            )

    def to_msg(self) -> list[dict]:
        """
        Convert to the format to be consumed by the model.
        Returns:
            List[Dict]: The message thread.
        """
        return self.messages

    def __str__(self):
        return pformat(self.messages, width=160, sort_dicts=False)

    def save_to_file(self, file_path):
        """
        Save the current state of the message thread to a file.
        Args:
            file_path (str): The path to the file.
        """
        Path(file_path).write_text(json.dumps(self.messages, indent=4))

    def get_round_number(self) -> int:
        """
        From the current message history, decide how many rounds have been completed.
        """
        completed_rounds = 0
        for message in self.messages:
            if message["role"] == "assistant":
                completed_rounds += 1
        return completed_rounds

    @classmethod
    def load_from_file(cls, file_path: str):
        """
        Load the message thread from a file.
        Args:
            file_path (str): The path to the file.
        Returns:
            MessageThread: The message thread.
        """
        with open(file_path) as f:
            messages = json.load(f)
        return cls(messages)


class FunctionCall:
    def __init__(self , tool_id , function_name , arguments):
        self.tool_id = f"tool_call_{tool_id}"
        self.function_name = function_name
        self.arguments = arguments
        self.result = None
        self.call_ok = False

    def arguments_to_string(self):
        if len(self.arguments) == 0:
            return ""
        
        arguments_string = ""
        for arg in self.arguments.values():
            if arguments_string == "":
                arguments_string += arg
            else:
                arguments_string += " , " + arg
        
        return arguments_string
    
    def __str__(self):
        return f"{self.function_name}({self.arguments_to_string()})"
    
    def __eq__(self, value):
        return self.function_name == value.function_name and self.arguments == value.arguments and self.result == value.result and self.call_ok == value.call_ok
    
    def parameters_to_str(self):
        if len(self.arguments) == 0:
            return ""
        
        parameter_list = []
        parameter_list.append(self.arguments['class_name'])
        if 'method_name' in self.arguments.keys():
            parameter_list.append(self.arguments['method_name'])
        else:
            parameter_list.append(self.arguments['field_name'])

        return parameter_list[0] + '.' + parameter_list[1]
    
    def to_dict(self):
        return {"func_name": self.function_name, "arguments": self.arguments}

    def set_result(self, function_result , call_ok):
        self.result = function_result
        self.call_ok = call_ok

    def to_dict_with_result(self):
        return {
            # "tool_call"
            # "tool_call_id": self.tool_id,
            "func_name": self.function_name,
            "arguments": self.arguments,
            "output": self.result,
            "call_ok": self.call_ok
        }

