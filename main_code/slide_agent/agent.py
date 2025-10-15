from google.adk.agents.sequential_agent import SequentialAgent
from google.adk.agents.callback_context import CallbackContext
from google.genai import types
from dotenv import load_dotenv

# —— 新的：编程求解循环 Agent
from .sub_agents.solver_agents.agent import solver_loop_agent

# 在模块顶部加载环境变量
load_dotenv('.env')


def _get_text_from_context(callback_context: CallbackContext) -> str | None:
    """拿到用户刚发来的纯文本。"""
    user_content = getattr(callback_context, "user_content", None)
    if user_content and getattr(user_content, "parts", None):
        for part in user_content.parts:
            text = getattr(part, "text", None)
            if text:
                return text
    return None


def before_agent_callback(callback_context: CallbackContext):
    """
    初始化：从文本解析 problem，并读取 metadata 中的配置（language/tests/max_attempts 等）。
    """
    state = callback_context.state
    metadata = state.get("metadata") or {}

    text = _get_text_from_context(callback_context)
    if not text:
        return types.Content(
            role="model",
            parts=[types.Part(text="请输入要解决的编程问题（可附上报错、输入输出示例或测试用例）。")]
        )

    # 读取可选元数据
    language = (metadata.get("language") or "python").lower()
    max_attempts = int(metadata.get("max_attempts", 8) or 8)
    timeout_sec = int(metadata.get("timeout_sec", 15) or 15)

    # 可选：标准输入/期望输出 或 自定义测试说明（Verifier/Discovery 会自动利用）
    tests = metadata.get("tests") or []  # 例如：[{"stdin": "3\n4\n", "expected_stdout": "7\n"}]
    constraints = metadata.get("constraints") or ""  # 性能/内存/风格等额外约束
    entrypoint_args = metadata.get("args") or []     # 运行参数
    stdin_text = metadata.get("stdin") or ""         # 默认标准输入

    # 初始化状态
    state["problem"] = text.strip()
    state["language"] = language
    state["timeout_sec"] = max(5, timeout_sec)
    state["tests"] = tests
    state["constraints"] = constraints
    state["default_args"] = entrypoint_args
    state["default_stdin"] = stdin_text

    # 运行与验证的中间态
    state["attempts"] = 0
    state["max_attempts"] = max(2, max_attempts)
    state["last_solution"] = None       # {filename, language, code, run_result, ...}
    state["last_run"] = None            # {ok, stdout, stderr, exit_code, ...}
    state["last_validation"] = None     # {verdict, reason, next_actions, ...}
    state["last_feedback"] = None       # Verifier 给 Discovery 的修复提示
    state["references"] = {}            # search_solver 写入的参考链接

    return None


root_agent = SequentialAgent(
    name="CodeProblemSolvingAgent",
    description="编程解决问题系统：自动检索→生成代码→运行→验证→迭代修复直至通过",
    sub_agents=[solver_loop_agent],
    before_agent_callback=before_agent_callback
)
