"""LLM 客户端：封装 Kimi（OpenAI 兼容）调用，错误分级重试。

重试策略：仅对临时性错误（429 限流 / 5xx 服务端 / 网络超时断连）做指数退避，
4xx（认证失败、参数错误等永久性错误）直接抛出，不做无意义重试。

openai / langchain_openai 为惰性导入：只有真正实例化 LLMClient 时才加载，
离线运行单元测试（test_report 等）不需要安装这些包。
"""
import json
import os
import re
import time

MAX_RETRIES = 3


def _classify(error: Exception) -> str:
    """把异常分为 transient（可重试）/ auth（认证）/ bad_request / other。"""
    import openai
    if isinstance(error, (openai.RateLimitError,       # 429 限流
                          openai.InternalServerError,  # 5xx 服务端错误
                          openai.APIConnectionError,   # 网络断连
                          openai.APITimeoutError)):    # 请求超时
        return "transient"
    if isinstance(error, openai.AuthenticationError):
        return "auth"
    if isinstance(error, openai.BadRequestError):
        return "bad_request"
    return "other"


class LLMClient:
    """对 ChatOpenAI 的薄封装：统一配置入口 + 分级重试 + 原始输出归档。

    归档：每次调用把 system/user/原始回复追加写入 logs/runs/<run_id>/llm_calls.jsonl，
    运行结果因此可复盘——演示时被问"这次结果能保证吗"，翻开归档就是证据。
    """

    def __init__(self, temperature: float = 1, run_dir: str | None = None):
        from langchain_openai import ChatOpenAI

        api_key = os.getenv("LLM_API_KEY")
        if not api_key or api_key.startswith("sk-在这里"):
            raise RuntimeError(
                "未检测到有效的 LLM_API_KEY。请复制 .env.example 为 .env 并填入你的 API Key。"
            )
        self.llm = ChatOpenAI(
            model=os.getenv("LLM_MODEL", "kimi-k2.6"),
            api_key=api_key,
            base_url=os.getenv("LLM_BASE_URL", "https://api.moonshot.cn/v1"),
            temperature=temperature,  # kimi-k2.6 仅支持 temperature=1
        )
        self._archive_path = None
        if run_dir:
            os.makedirs(run_dir, exist_ok=True)
            self._archive_path = os.path.join(run_dir, "llm_calls.jsonl")

    def chat(self, system: str, user: str) -> str:
        """单轮问答：system 定角色，user 给任务，返回文本。临时错误自动重试。"""
        messages = [("system", system), ("human", user)]
        last_error = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                reply = self.llm.invoke(messages).content
                self._archive(system, user, reply)
                return reply
            except Exception as e:
                kind = _classify(e)
                if kind == "transient":
                    last_error = e
                    wait = self._retry_after(e) or 2 ** (attempt - 1)  # 1s, 2s, 4s
                    print(f"  [重试] 第 {attempt} 次调用失败（临时性错误），{wait:.0f} 秒后重试...")
                    time.sleep(wait)
                    continue
                if kind == "auth":
                    raise RuntimeError("LLM 认证失败（401）：请检查 .env 中的 LLM_API_KEY 是否正确。")
                if kind == "bad_request":
                    raise RuntimeError(f"LLM 请求参数有误（400）：{e}")
                raise RuntimeError(f"LLM 调用失败：{e}")
        raise RuntimeError(f"连续 {MAX_RETRIES} 次调用 LLM 均失败：{last_error}")

    def _archive(self, system: str, user: str, reply: str) -> None:
        """把一次成功调用的原始输入输出追加到归档文件（JSONL，一行一条）。"""
        if not self._archive_path:
            return
        record = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                  "system": system, "user": user, "reply": reply}
        try:
            with open(self._archive_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError:  # 归档失败不阻断主流程
            pass

    @staticmethod
    def _retry_after(error: Exception) -> float | None:
        """如果服务器返回了 Retry-After 头，按它的提示等待（封顶 30 秒）。"""
        response = getattr(error, "response", None)
        if response is None:
            return None
        value = response.headers.get("Retry-After") if response.headers else None
        if value:
            try:
                return min(float(value), 30.0)
            except ValueError:
                return None
        return None


def _strip_json_comments(text: str) -> str:
    """去掉 JSON 中的 // 行注释，但跳过字符串字面量内部。

    不能用正则直接删：字符串值里的 "https://..." 会被误判为注释。
    这里逐字符扫描，只在字符串之外才把 // 到行尾的内容剥掉。
    """
    out: list[str] = []
    i, n = 0, len(text)
    in_string = False
    while i < n:
        ch = text[i]
        if in_string:
            out.append(ch)
            if ch == "\\" and i + 1 < n:  # 转义字符原样保留下一个字符
                out.append(text[i + 1])
                i += 2
                continue
            if ch == '"':
                in_string = False
            i += 1
            continue
        if ch == '"':
            in_string = True
            out.append(ch)
            i += 1
        elif ch == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != "\n":  # 跳过注释到行尾
                i += 1
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def extract_json(text: str) -> dict | None:
    """从 LLM 输出中容错提取 JSON 对象，失败返回 None。"""
    if not text:
        return None
    # 优先匹配 ```json ... ``` 代码块
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidate = fenced.group(1) if fenced else None
    if candidate is None:
        # 退化：取最外层大括号范围
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            candidate = text[start:end + 1]
    if not candidate:
        return None
    candidate = _strip_json_comments(candidate)
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        return None
