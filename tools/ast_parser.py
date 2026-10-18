"""工具1：AST 结构解析，提取函数/类/导入，定位语法错误。复用自项目1。"""
import ast


def ast_structure_parser(code: str) -> str:
    """解析一段 Python 代码的 AST 结构，返回函数、类、导入列表及语法错误信息。"""
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return (
            f"【语法错误】第 {e.lineno} 行第 {e.offset} 列：{e.msg}\n"
            f"出错内容：{(e.text or '').strip()}"
        )
    except Exception as e:
        return f"【解析失败】{type(e).__name__}: {e}"

    functions, classes, imports = [], [], []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            args = [a.arg for a in node.args.args]
            functions.append(f"函数 {node.name}({', '.join(args)}) 定义于第 {node.lineno} 行")
        elif isinstance(node, ast.AsyncFunctionDef):
            functions.append(f"异步函数 {node.name}() 定义于第 {node.lineno} 行")
        elif isinstance(node, ast.ClassDef):
            classes.append(f"类 {node.name} 定义于第 {node.lineno} 行")
        elif isinstance(node, ast.Import):
            imports.extend(f"import {a.name}" for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.append(f"from {node.module} import {', '.join(a.name for a in node.names)}")

    lines = ["===== AST 结构解析结果 =====", f"代码总行数：{len(code.splitlines())}"]
    lines.append("\n-- 导入 --")
    lines.extend(imports or ["（无）"])
    lines.append("\n-- 类 --")
    lines.extend(classes or ["（无）"])
    lines.append("\n-- 函数 --")
    lines.extend(functions or ["（无）"])
    return "\n".join(lines)
