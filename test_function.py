from tree_sitter import Language , Parser
from tree_sitter_languages import get_language

JAVA_LANGUAGE = get_language("java")
parser = Parser()
parser.set_language(JAVA_LANGUAGE)

source_code = '''
package org.jfree.chart.axis;
import com.example.classA;
import java.util.*;
import static java.lang.Math.*;
import static java.lang.Math.PI;

@Data
public class LLMGeneratedTests {
/** Reusable Long constant for zero. */
    public static final Long LONG_ZERO = Long.valueOf(0L);
    /** Reusable Long constant for one. */
    public static final Long LONG_ONE = Long.valueOf(1L);
    private int x = 1 , y = 2 , z;
    ClassA a1 = b;
    ClassA a2 = new ClassA();
    ClassA a3 = getClassA();
    ClassA.ClassB a4 = new ClassA.ClassB();
    String s = new String("Hello");
    s1 = getName(a , b);
    boolean flag = True;

    public static ResResult<UserVO> register(UserVO userVO , final @Nonull int x , String str){
        a(true);
        return true;
    }
}
'''

source_code_2 = '''
package org.jfree.chart.axis;
import com.example.classA;
import java.util.*;
import static java.lang.Math.*;
import static java.lang.Math.PI;

@Data
public interface LLMGeneratedTests {
/** Reusable Long constant for zero. */
    public static final Long LONG_ZERO = Long.valueOf(0L);
    /** Reusable Long constant for one. */
    public static final Long LONG_ONE = Long.valueOf(1L);
    private int x = 1 , y = 2 , z;
    ClassA a1 = b;
    ClassA a2 = new ClassA();
    ClassA a3 = getClassA();
    ClassA.ClassB a4 = new ClassA.ClassB();
    String s = new String("Hello");
    s1 = getName(a , b);
    boolean flag = True;

    public static ResResult<UserVO> register(UserVO userVO , final @Nonull int x , String str){
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
    print("yes" , child.type)
    if child.type == 'modifiers':
        for mod_child in child.children:
            # if mod_child.type == 'method_modifier':
            #     method_modifiers.append(mod_child.text.decode())
            # print(mod_child.text.decode())
            method_modifiers.append(mod_child.text.decode())
    # elif child.type == 'identifier':
    #     print(child.text.decode())

print(method_modifiers)

return_node = method_node.child_by_field_name('type')
print(return_node)

name_node = method_node.child_by_field_name('name')
print(name_node.text.decode())

parameters_node = method_node.child_by_field_name('parameters')
print(parameters_node.text.decode())

m_list = []
p_list = []
for i , child in enumerate(parameters_node.children):
    if child.type == 'formal_parameter':
        flag = False
        for child_child in child.children:
            if child_child.type == 'modifiers':
                flag = True
                print(child_child.text.decode())
                m_list.append(child_child.text.decode())

        if not flag:
            m_list.append("no modifier")

        print(i , end=': ')
        parameter_node = child.child_by_field_name('type')
        text = child.text.decode()
        print(parameter_node.text.decode())
        p_list.append(parameter_node.text.decode())

print(m_list)
print(p_list)
# if "public" in method_modifiers:
#     is_target = True

# print(method_modifiers)
print(",".join((modifier.strip() + ' ' if modifier != "no modifier" else "") + item for modifier , item in zip(m_list , p_list)))

for child in root_node.children:
    if child.type == 'import_declaration':
        import_node = child
        for child_node in import_node.children:
            print(child_node.type , child_node.text.decode())
            if child_node.type == 'scoped_identifier':
                print("scope: " , child_node.child_by_field_name('scope').text.decode())
                print("name: " , child_node.child_by_field_name('name').text.decode())


print("-----------------------")

tree_2 = parser.parse(bytes(source_code_2, "utf8"))
root_node_2 = tree_2.root_node

class_node = None
for child in root_node_2.children:
    if child.type == 'interface_declaration':
        class_node = child
        break

for child in class_node.child_by_field_name('body').children:
    if child.type in ['field_declaration' , 'constant_declaration']:
        print(child.type , child.text.decode())