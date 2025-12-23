from tree_sitter import Language , Parser
from tree_sitter_languages import get_language

JAVA_LANGUAGE = get_language("java")
parser = Parser()
parser.set_language(JAVA_LANGUAGE)

source_code = '''
package org.jfree.chart.axis;
import com.example.classA;
import java.util.*;
import static java.lang.Math.abs;

@Data
public class LLMGeneratedTests {
    private int x = 1 , y = 2 , z;
    ClassA a1 = b;
    ClassA a2 = new ClassA();
    ClassA a3 = getClassA();
    ClassA.ClassB a4 = new ClassA.ClassB();
    String s = new String("Hello");
    s1 = getName(a , b);
    boolean flag = True;

    public static ResResult<UserVO> register(UserVO userVO){
        a(true);
        return true;
    }
}
'''

tree = parser.parse(bytes(source_code, "utf8"))
root_node = tree.root_node

class_node = None
for child in root_node.children:
    if child.type == 'class_declaration':
        class_node = child
        break

class_name = class_node.child_by_field_name('name').text.decode()
class_content = class_node.text.decode()

method_node = None
for child in class_node.child_by_field_name('body').children:
    if child.type == 'method_declaration':
        method_node = child
        break

method_modifiers = []
is_target = False
for child in method_node.children:
    # print(child.type)
    # if child.type == 'method_modifier':
    #     method_modifiers.append(child.text.decode())
    print(child.type)
    if child.type == 'modifiers':
        for mod_child in child.children:
            # if mod_child.type == 'method_modifier':
            #     method_modifiers.append(mod_child.text.decode())
            # print(mod_child.text.decode())
            method_modifiers.append(mod_child.text.decode())

return_node = method_node.child_by_field_name('type')
print(return_node)

# if "public" in method_modifiers:
#     is_target = True

# print(method_modifiers)