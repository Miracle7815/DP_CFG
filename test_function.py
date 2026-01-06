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
public class LLMGeneratedTests extends FatherClass implements FatherInterface{
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
    int[][] array1 = new int[5][5];
    List<String> list = new ArrayList<>();

    // Long.getLong(String,int)
    // Long.getLong(String,Integer)
    // Long.valueOf(String,int)
    // Long.valueOf(String)
    // Short.valueOf(String)
    // Short.decode(String)
    // Short.valueOf(String,int)
    // Short.valueOf(String)
    // new BigDecimal(String)
    // new BigInteger(String)
    // new BigInteger(String,int radix)
    // Possible inputs:
    // 45 45.5 45E7 4.5E7 Hex Oct Binary xxxF xxxD xxxf xxxd
    // plus minus everything. Prolly more. A lot are not separable.

    /**
     * <p>Convert a <code>String</code> to an <code>int</code>, returning
     * <code>zero</code> if the conversion fails.</p>
     *
     * <p>If the string is <code>null</code>, <code>zero</code> is returned.</p>
     *
     * <pre>
     *   NumberUtils.toInt(null) = 0
     *   NumberUtils.toInt("")   = 0
     *   NumberUtils.toInt("1")  = 1
     * </pre>
     *
     * @param str  the string to convert, may be null
     * @return the int represented by the string, or <code>zero</code> if
     *  conversion fails
     * @since 2.1
     */
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
public interface LLMGeneratedTests extends FatherInterface_1 , FatherInterface_2{
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

    public static ResResult<UserVO> register(UserVO userVO , final @Nonull int x , String str) {
        a(true);
        return true;
    }
}
'''

tree = parser.parse(bytes(source_code, "utf8"))
root_node = tree.root_node

# for child in root_node.children:
#     print(child.type , child.text.decode())

# print("-----------------------")

class_node = None
for child in root_node.children:
    if child.type == 'class_declaration':
        class_node = child
        break

# for child in class_node.children:
#     print(child.type , child.text.decode())

# print("-----------------------")

class_name = class_node.child_by_field_name('name').text.decode()
class_content = class_node.text.decode()

method_node = None
for child in class_node.child_by_field_name('body').children:
    if child.type == 'method_declaration':
        method_node = child
        break

for child in class_node.child_by_field_name('body').children:
    print(child.type , child.text.decode())

print("-----------------------")

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


# for child in class_node.children:
#     node = child.child_by_field_name('superclass')
#     print(node.text.decode() if node else None)

super_class_node = class_node.child_by_field_name('superclass')
for child in super_class_node.children:
    print(child.type , child.text.decode())


for child in class_node.children:
    if child.type == 'super_interfaces':    # implements interface
        superclass_node = child
        for child_child in superclass_node.children:
            print(child_child.type , child_child.text.decode())

for child in class_node.child_by_field_name('body').children:
    if child.type in ['field_declaration' , 'constant_declaration']:
        type_node = child.child_by_field_name('type')
        print(type_node.type , end=': ')
        for var_child in child.children:
            print(var_child.type , var_child.text.decode() , end="  ")
            if var_child.type == 'variable_declarator':
                var_name_node = var_child.child_by_field_name('name')
                print("name: " , var_name_node.text.decode() , end="  ")
                var_value_node = var_child.child_by_field_name('value')
                if var_value_node:
                    print("value: " , var_value_node.type , var_value_node.text.decode() , end="  ")
                    if var_value_node.type == 'object_creation_expression':
                        obj_type_node = var_value_node.child_by_field_name('type')
                        print("object type: " , obj_type_node.text.decode() , end="  ")
                        for child_child in var_value_node.children:
                            print("yes ", child_child.type , child_child.text.decode() , end="  ")
        print()


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

for child in class_node.children:
    print(child.type , child.text.decode())