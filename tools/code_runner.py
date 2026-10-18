"""工具3：在独立子进程中运行代码，10 秒超时保护。复用自项目1。"""
import os
import subprocess
import sys
import tempfile

TIMEOUT_SECONDS = 10


def python_code_executor(code: str) -> str:
    """在隔离子进程中运行一段 Python 代码，返回退出码、标准输出和标准错误。"""
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", encoding="utf-8", delete=False
        ) as f:
            f.write(code)
            tmp_path = f.name

        result = subprocess.run(
            [sys.executable, tmp_path],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
        )
        output = ["===== 代码运行结果 =====", f"退出码：{result.returncode}"]
        output.append(result.stdout.strip() or "（无输出）")
        if result.returncode != 0 or result.stderr.strip():
            output.append("-- 标准错误 --")
            output.append(result.stderr.strip() or "（无）")
        return "\n".join(output)
    except subprocess.TimeoutExpired:
        return f"===== 代码运行结果 =====\n运行超时（>{TIMEOUT_SECONDS} 秒）被强制终止，代码可能存在死循环。"
    except Exception as e:
        return f"===== 代码运行结果 =====\n执行器异常：{type(e).__name__}: {e}"
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def python_module_executor(module_code: str, test_code: str) -> str:
    """模块级验证：把被测代码存为模块，在同目录运行测试脚本（回归验证用）。

    测试脚本可通过 import module_under_test 引用被测代码中的函数/类。
    """
    tmp_dir = None
    try:
        tmp_dir = tempfile.mkdtemp()
        with open(os.path.join(tmp_dir, "module_under_test.py"), "w", encoding="utf-8") as f:
            f.write(module_code)
        test_path = os.path.join(tmp_dir, "run_checks.py")
        with open(test_path, "w", encoding="utf-8") as f:
            f.write(test_code)

        result = subprocess.run(
            [sys.executable, test_path],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
            cwd=tmp_dir,
        )
        output = ["===== 回归验证结果 =====", f"退出码：{result.returncode}"]
        output.append(result.stdout.strip() or "（无输出）")
        if result.returncode != 0 or result.stderr.strip():
            output.append("-- 标准错误 --")
            output.append(result.stderr.strip() or "（无）")
        return "\n".join(output)
    except subprocess.TimeoutExpired:
        return f"===== 回归验证结果 =====\n运行超时（>{TIMEOUT_SECONDS} 秒）被强制终止。"
    except Exception as e:
        return f"===== 回归验证结果 =====\n执行器异常：{type(e).__name__}: {e}"
    finally:
        if tmp_dir:
            import shutil
            shutil.rmtree(tmp_dir, ignore_errors=True)
