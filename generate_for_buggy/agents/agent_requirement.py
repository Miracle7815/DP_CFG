# for requirement generation
import json
import os
import re
from copy import deepcopy
from ..config import logger
from ..basic_class.llm_message import MessageThread , FunctionCall
from ..models import model
from .agent_reviewer import ReviewAgent

SYSTEM_PROMPT = '''You are an experienced software test engineer specialized in requirement analysis and test specification generation.
Your task is to analyze Java method with Javadoc and generate comprehensive requirement documents that will guide test case generation.
'''

CONTEXT_SYSTEM_PROMPT = '''You are an experienced software test engineer specialized in test specification generation and static analysis.
Your task is to analyze Java method (may contain bugs) and gather the necessary code context for Target method.
This context will later be used to generate test requirement.
'''

# CONTEXT_COLLECTION_PROMPT = '''**CRITICAL PROTOCOL: GATHER INFO FIRST**
# Do NOT hallucinate or guess. 
# Before generating the final specification, you MUST:
# 1. Scan the method code for any helper methods, external constants, or class fields that are undefined in the snippet.
# 2. Use the provided tools (functions) to retrieve their definitions.
# 3. Only when you have a complete understanding of the context, proceed to generate the final Markdown document.
# '''

# TARGET_METHOD_INFO = '''** Method under Test **:
#    - Class name: {class_name}
#    - Import: {import_list}
#    - Method signature: {method_signature}
#    - Java doc : {javadoc}
#    - Source code: {source_code}
#    - Call methods:{call_methods}
# '''

TARGET_METHOD_INFO = '''** Method under Test **:
   - Class name: {class_name}
   - Import: {import_list}
   - Method signature: {method_signature}
   - Java doc : {javadoc}
   - Source code: {source_code}
'''

TOOL_PROMPT = '''The method may rely on class fields, constants, or other methods that are not visible in the snippet provided.
You should use tools to retrieve the defination of what you need. 

### Constraint:
- Collect context ONLY from current project, Do NOT analyze the internal implementation of Java standard library methods.
- Do NOT recursively analyze all called methods. Only extract the called method if they affect the observable behavior of the target method.

### Available Tools:
- `search_method_source(class_name , method_name)`: {
    "description": get source code of method in class.
    "parameters": {
        "class_name": {
            "type": "string",
            "description": "Whole class name where the method is defined refering to import map. (eg: org.apache.commons.lang3.AnnotationUtils)"
        },
        "method_name": {
            "type": "string",
            "description": "The exact name of method (eg: isValid)"
        }
    }
}
- `search_field_defination(class_name , field_name)`: {
    "description": get source code of method in class.
    "parameters": {
        "class_name": {
            "type": "string",
            "description": "Whole class name where the field is defined refering to import map. (eg: org.apache.commons.lang3.AnnotationUtils)"
        },
        "field_name": {
            "type": "string",
            "description": "The exact name of field (eg: MAX_VALUE)"
        }
    }
}
- `search_use_example()`: {
    "description": get the called example of method under test.
    "parameters" : {
    }
}

### Output Format:
1. If you have gathered all necessary information, ONLY output the special token: `[ANALYSIS_COMPLETE]`
2. If you want to call tools , Print output following the strict format:
{
    "tool_calls":[
        {
            "tool_name": tool_name_1,
            "args": {
                "parameter_1_name": parameter_1,
                "parameter_2_name": parameter_2
            }
        },
        {
            "tool_name": tool_name_2,
            "args": {
            }
        }
    ]
}
'''  

REQUIREMENT_PROMPT = '''*** Write requirement for test ***
The Target Method you are analyzing belongs to a codebase that is UNKNOWN to contain bugs. 
Therefore, you must NOT assume the actual code implementation is correct.
You must enforce a strict separation between **Explicit Requirements** and **Inferences**.
You must gather information from:
1. The **Javadoc** (Documentation comments) - This is the primary source of truth.
2. The **Method Signature** (Name, Return Type, Parameters).
3. The **Method Inplemention** (Semantic meaning but Target method implementation MAY contain bugs)
4. The **Context** (Provided by your tool call above).

*** OUTPUT FORMAT ***:
## 1. Method Intent: A concise , high-level summary of what the method SHOULD do.
## 2. Input/Output Specification:
   - **Parameters**: Details the inputs required by the code, including input types, formats, and any constraints.
   - *Return Value*: Expected return values including output types, formats, and any constraints.
## 3. Logic Rules and Branches(Not code path): Describe the core functional paths defined in the Javadoc and Derive all certain logic rules and branches ONLY from:
   - the Javadoc
   - the method name and parameters
   - the explicit domain semantics
   Do NOT infer new business rules from the implementation.
## 4. Boundary Analysis: What should happen on null inputs, empty inputs, invalid inputs, or extreme values (Not specific numerical values)?
## 5. Suspicious Logic (Crucial): Use this section to guide the Test engineer on what to test
   - List undocumented behaviors that are NOT clearly defined in Javadoc but implemented in Source Code as `[Undocumented]`.
   - If you find the code implementation contradicts the Javadoc, explicitly point it out here as `[Contradiction]`.

** Important **: 
- If the Javadoc contradicts the Code, TRUST THE JAVADOC.
- For Sections 1, 2, 3 and 4(Intent, I/O, Logic Rules, Boundary analysis)**:
    - You must **ONLY** use information that is **EXPLICITLY stated** in the Javadoc or **STRICTLY implied** by the method signature.
    - Important: Do not infer behavior based on "standard Java conventions" or the actual code implementation in these sections.
    - If the Javadoc does not specify behavior for a scenario, you can explicitly write: "Not Specified in Javadoc" but not "Should".

- For Section 5 (Suspicious Logic)**:
    - This is the ONLY section where you are allowed to look at the Source Code or apply "Common Sense".
'''

UPDATE_REQUIREMENT_WITH_REVIEW = '''
'''

# '''
#     -   Use this section to list:
#         -   Behaviors implemented in code but missing from Javadoc.
#         -   Contradictions between Code and Javadoc.
#         -   Standard conventions (e.g., "Javadoc is silent on null, but standard practice suggests NPE").
#         -   If the Javadoc does not specify behavior for a scenario (e.g., input is null), you can explicitly write: "Not Specified in Javadoc" but not "Should throw Exception".
# '''

BASE_PATH = os.path.join(os.path.dirname(__file__) , '..' , '..' , 'results' , 'detailed_res_info')

class RequirementAgent:
    def __init__(self , project_name , all_packages , class_map , method_map):
        self.project_name = project_name
        self.all_packages = all_packages
        self.class_map = class_map
        self.method_map = method_map

        self.context_request_idx = -1
        self.responses = {}

        self.context_request_memory = []
        # self.history = {}
        # self.feedbacks = {}
        self.target_method = None

    def collect_call_function_info(self , target_method , call_method_name , parameters):
        called_methods = []
        for method in target_method.called_methods:
            if method.name_no_package == call_method_name and len(parameters) == len(method.parameters_list):
                called_methods.append(method)

        return called_methods

    def construct_init_thread(self):
        thread = MessageThread()    

        return thread

    def analyse_tool_calls(self , response):
        pattern = re.compile(r"```json\s*([\s\S]*?)\s*```", re.IGNORECASE)
        results = []

        for m in pattern.finditer(response):
            raw = m.group(1)
            results.append(json.loads(raw))

        return results

    def search_method_source(self , class_name , method_name):
        result = []

        for classs_name , classs in self.class_map.items():
            if classs_name == class_name:
                for method in classs.methods:
                    if method.name_no_package == method_name and method in self.target_method.called_methods:
                        result.append(method)
                break

        return result

    def search_field_defination(self , class_name , field_name):
        for classs_name , classs in self.class_map.items():
            if classs_name == class_name:
                for field , statement in classs.fields.items():
                    if field == field_name:
                        return statement
                    
        return None
    
    def search_use_example(self):
        results = []
        for method in self.target_method.callee_methods:
            method_content = method.content
            method_content = method_content.split('\n')
            line_count = 0
            for line in method_content:
                if line.strip() != "":
                    line_count += 1
                
            if line_count <= 20:
                results.append(method.content)
            
            if len(results) == 3:
                return results
        
        for method in self.method_map.values():
            for method_info in method.called_method_site:
                if method_info[0] == self.target_method.name and method_info[1] == tuple(self.target_method.parameters_list):
                    if method.content in results:
                        continue
                    
                    start_line = method.node.start_point[0]
                    end_line = method.node.end_point[0]
                    start = min(start_line , method_info[2] - 10)
                    end = max(end_line , method_info[2] + 10)

                    method_content = method_content.split("\n")[start - start_line : end - start_line]
                    method_content = "\n".join(method_content)

                    results.append(method_content)

                    if len(results) == 3:
                        return results
        
        return results
    

    def write_requirement_for_method(self , target_method):
        logger.info("--------- Stage 1: Context gathering ---------")
        self.target_method = target_method
        context_thread = self.construct_init_thread()
        requirement_thread = self.construct_init_thread()
        # thread.add_system(SYSTEM_PROMPT)
        context_thread.add_system(CONTEXT_SYSTEM_PROMPT + '\n' + TOOL_PROMPT)

        user_prompt = None
        prefix_prompt = '''Here is the target method under test:\n'''
        
        javadoc = target_method.javadoc if target_method.javadoc is not None else "No Java doc" 
        import_string = ""
        for import_info in target_method.import_map.values():
            import_string += import_info + "\n"
        
        import_string = import_string if import_string != "" else "No import information"

        call_methods_string = ""
        for called_method in self.target_method.called_methods:
            call_methods_string += f"{called_method.signature}\n"

        call_methods_string = call_methods_string if call_methods_string != "" else "No called methods"

        method_info = TARGET_METHOD_INFO.format(class_name=target_method.belong_class , import_list=import_string , method_signature=target_method.signature , javadoc=javadoc  , source_code=target_method.content , call_methods=call_methods_string)
        
        user_prompt = prefix_prompt + method_info
        context_thread.add_user(user_prompt)
        requirement_thread.add_user("Here is the information about method under test\n" + method_info)
        
        logger.info(f"Requirement Prompt: \n{context_thread.to_msg()}")

        response , *_ = model.SELECTED_MODEL.call(context_thread.to_msg())
        
        logger.info(f"Requirement Agent response:\n{response}")

        max_try = 10
        tool_id = 0
        flag = False

        function_list = []
        function_string = []

        for try_num in range(max_try): 
            if "ANALYSIS_COMPLETE" in response:
                response = "[ANALYSIS_COMPLETE]"
                flag = True
                break

            results = self.analyse_tool_calls(response)  
            if results is not []:
                function_calls = []
                tool_calls = results[0]['tool_calls']
                for tool_call in tool_calls:
                    function_name = tool_call['tool_name'].strip()
                    arguments = tool_call['args']
                    function_call = FunctionCall(tool_id , function_name , arguments)
                    tool_id += 1

                    function_calls.append(function_call)
                
                # thread.add_model(None , function_calls)
                # thread.add_model(response)
                
                call_results = {"tool_calls": []}
                for function_call in function_calls:
                     
                    if function_call.function_name == "search_method_source":
                        try:
                            class_name = function_call.arguments['class_name']
                            method_name = function_call.arguments['method_name']
                            function_result = self.search_method_source(class_name , method_name)
                            if len(function_result) != 0:
                                function_call.set_result(function_result , True)
                            else:
                                function_call.set_result("Method not found" , True)
                        except Exception as e:
                            function_call.set_result(str(e) , False)
                    elif function_call.function_name == "search_field_defination":
                        try:
                            class_name = function_call.arguments['class_name']
                            field_name = function_call.arguments['field_name']
                            function_result = self.search_field_defination(class_name , field_name)
                            if function_result is not None:
                                function_call.set_result(function_result , True)
                            else:
                                function_call.set_result("Field not found" , True)
                        except Exception as e:
                            function_call.set_result(str(e) , False)
                    elif function_call.function_name == "search_use_example":
                        try:
                            function_result = self.search_use_example()
                            if function_result != []:
                                function_result_string = ""
                                for i , result in enumerate(function_result):
                                    function_result_string += f"Example {i + 1}:\n{result}\n"
                                function_call.set_result(function_result_string , True)
                            else:
                                function_call.set_result("No use example" , True)
                        except Exception as e:
                            function_call.set_result(str(e) , False)
                    else:
                        function_call.set_result("Tool not found" , False)

                    call_results["tool_calls"].append(function_call)

                # thread.add_user(json.dumps(call_results , ensure_ascii=False) + "\n" + TOOL_PROMPT)
                # thread.add_user(json.dumps(call_results , ensure_ascii=False))
                call_results_content = "Here is the context information you need:\n**Context information**\n"
                
                for call_result in call_results['tool_calls']:
                    if call_result.call_ok is False:
                        continue
                    if call_result not in self.context_request_memory:
                        self.context_request_memory.append(call_result)

                # for call_result in call_results["tool_calls"]:
                #     flag = True
                #     if str(call_result) not in function_list:
                #         function_list.append(str(call_result))
                #         flag = False

                #     content_string = ''
                #     if call_result.function_name == "search_use_example":
                #         content_string += "## " + self.target_method.name + " Usage example:\n"
                #         content_string += call_result.result + "\n"
                #         call_results_content += content_string
                #     else:
                #         content_string += f"## {call_result.parameters_to_str()} :\n{call_result.result}\n"
                #         call_results_content += content_string

                #     if flag is False:
                #         function_string.append(content_string)

                # context_thread.add_user(call_results_content)
            context_thread.pop_message()
                
            new_prompt = "\nHere is the context information you have gathered:\n"
            context_string = ""
            for tool_call in self.context_request_memory:
                if tool_call.function_name == "search_use_example":
                    context_string += "## " + self.target_method.name + " Usage example:\n" + tool_call.result + '\n'
                else:
                    if tool_call.result == "Method not found" or tool_call.result == "Field not found":
                        context_string += "## " + tool_call.arguments[0] + '.' + tool_call.arguments[1] + " : " + tool_call.result + "\n"
                    elif tool_call.function_name == "search_field_defination":
                        context_string += "## " + tool_call.arguments[0] + '.' + tool_call.arguments[1] + " : " + tool_call.result + "\n"
                    elif tool_call.function_name == "search_method_source":
                        for method in tool_call.result:
                            context_string += "## " + method.signature + " :\n" + method.content + "\n"

            new_prompt = method_info + new_prompt + context_string
            context_thread.add_user(new_prompt)

            logger.info(f"Requirement Prompt: \n{context_thread.to_msg()}")
            response , *_ = model.SELECTED_MODEL.call(context_thread.to_msg())
            logger.info(f"Requirement Agent response:\n{response}")

        # if flag is False:
        #     context_thread.add_user("Exceeding the maximum number of tool calls limit")

        requirement_thread.add_user("Here is the context information you need:\n**Context information**\n" + ''.join(function_string))

        group = self.project_name.split('_')[0]
        dir_path = os.path.join(BASE_PATH , group , self.project_name)
        save_to_file(dir_path , 'requirement_agent_context_gather_chat.json' , json.dumps(context_thread.messages, indent=4))

        max_update = 5

        review_agent = ReviewAgent('requirement')
        # logger.info(f"Requirement Prompt: \n{REQUIREMENT_PROMPT}")
        requirement_thread.add_user(REQUIREMENT_PROMPT)

        # first generate
        flag = False
        while not flag:
            response , _ , _ , reason = model.SELECTED_MODEL.call(requirement_thread.to_msg())
            # thread.add_model(response)
            if reason == 'length':
                flag = False
                continue
            else:
                flag = True
            logger.info(f"Requirement Prompt: \n{requirement_thread.to_msg()}")
            logger.info(f"Requirement Agent response:\n{response}")
            group = self.project_name.split('_')[0]
            dir_path = os.path.join(BASE_PATH , group , self.project_name)
            # save_to_file(dir_path , 'requirement_agent_chat.txt' , json.dumps(thread.messages, indent=4))
            save_to_file(dir_path , "requirement_document.txt" , response)

        update_num = 0
        history_issue = []
        while update_num < max_update:
            # logger.info(f"Requirement Prompt: \n{thread.to_msg()}")
            # response , *_ = model.SELECTED_MODEL.call(thread.to_msg())
            # # thread.add_model(response)
            # logger.info(f"Requirement Agent response:\n{response}")
            # group = self.project_name.split('_')[0]
            # dir_path = os.path.join(BASE_PATH , group , self.project_name)
            # save_to_file(dir_path , 'requirement_agent_chat.txt' , json.dumps(thread.messages, indent=4))
            # save_to_file(dir_path , "requirement_document.txt" , response)
            file_path = os.path.join(dir_path , "requirement_document.txt")
            with open(file_path , 'r' , encoding='utf-8') as f:
                previous_requirement = f.read()
                f.close()

            review_results = review_agent.review_requirement(self.project_name , self.target_method)

            if review_results is None:
                break
            
            if review_results['result'].strip() == "Yes":
                break
            
            # previous_requirement = response

            prefix_prompt = f"** Previous Requirement **\n{previous_requirement}\n"

            if update_num != 0:
                requirement_thread.pop_message()
                # requirement_thread.pop_message()
            
            # requirement_thread.add_model(prefix_prompt)

            issue_text = ""

            history_message = ""
            for idx , issue in enumerate(history_issue):
                history_message += f'{idx + 1}. {issue}\n'

            new_issue_num = len(review_results)
            for idx , issue in enumerate(review_results['issues']):
                issue_desciption = f'''**Section: {issue['issue_section']}**
                - Error analysis: {issue['error_analysis']}
                '''
                issue_text += f'{idx + 1}. ' + issue_desciption
                if issue_desciption not in history_issue:
                    history_issue.append(issue_desciption)

            if update_num != 0:
                feedback_prompt = f'''*** CRITICAL REVIEW FEEDBACK ***

        Your previous requirement draft was **REJECTED** by the Lead Auditor.
        You must RE-WRITE the requirement to address the following specific issues:

        ### CURRENT CRITICAL ISSUES
        {issue_text}

        ### HISTORY OF MISTAKES
        {history_message}
        
        Please generate the **FULL, CORRECTED** Test Specification Document.
    '''
            elif update_num == 0:
                feedback_prompt = f'''*** CRITICAL REVIEW FEEDBACK ***

        Your previous requirement draft was **REJECTED** by the Lead Auditor.
        You must RE-WRITE the requirement to address the following specific issues:

        ### CURRENT CRITICAL ISSUES
        {issue_text}
        
        Please generate the **FULL, CORRECTED** Test Specification Document.
    '''

    #             **INSTRUCTIONS FOR REVISION:**
    # 1.  **Focus on the Feedback**: Fixing the issues listed above.
    # 2.  **Preserve Correct Parts**: Do not change sections that were not mentioned in the issues.
    # 3.  **Strict Adherence**: Remember the rules — Section 1-4 must derive from javadoc and method signature.

            requirement_thread.add_user(feedback_prompt)
            # logger.info(f"Requirement Update Prompt: \n{feedback_prompt}")
            logger.info(f"Requirement Prompt: \n{requirement_thread.to_msg()}")
            response , _ , _ , reason = model.SELECTED_MODEL.call(requirement_thread.to_msg())
            # thread.add_model(response)
            if reason == 'length':
                if update_num == 0:
                    requirement_thread.pop_message()
                    history_issue = history_issue[: -new_issue_num]
                # requirement_thread.pop_message()
                logger.info(f"Retry !!!")
                continue
            logger.info(f"Requirement Agent response:\n{response}")
            group = self.project_name.split('_')[0]
            dir_path = os.path.join(BASE_PATH , group , self.project_name)
            # save_to_file(dir_path , 'requirement_agent_chat.txt' , json.dumps(thread.messages, indent=4))
            save_to_file(dir_path , "requirement_document.txt" , response)
            update_num += 1


def save_to_file(dir_path , file_name , content):
    os.makedirs(dir_path , exist_ok=True)
    with open(os.path.join(dir_path , file_name) , 'w' , encoding='utf-8') as f:
        f.write(content)
        f.close()