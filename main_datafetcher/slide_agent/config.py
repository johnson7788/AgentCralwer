# 项目的基本配置

CONTENT_WRITER_AGENT_CONFIG = {
    "provider": "openai",
    "model": "gpt-4.1",
}
CHECKER_AGENT_CONFIG = {
    "provider": "deepseek",
    "model": "deepseek-chat",
}

# —— 新：创新发现Agent配置 ——
DISCOVERY_AGENT_CONFIG = {
    "provider": "openai",
    "model": "gpt-4.1",
}
VALIDATOR_AGENT_CONFIG = {
    "provider": "deepseek",   # 验证环节更偏搜索与判断
    "model": "deepseek-chat",
}