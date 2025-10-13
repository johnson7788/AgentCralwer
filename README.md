# spiderme - 多智能体Web数据抓取框架

`spiderme` 是一个基于多 Agent 协作的智能化Web数据抓取框架。它利用大型语言模型（LLM）的能力，模拟分析、执行、反思和发现的工作流，以自动化和动态的方式解决复杂的数据提取任务。

## 核心架构

项目的核心是四个协同工作的 Agent：

-   **`AnalyzerAgent` (分析器)**: 负责理解用户需求。它通过规则匹配、向量搜索和 LLM 推理相结合的方式，从可用的工具库（MCPs）中选择最合适的解决方案。

-   **`ExecutorAgent` (执行器)**: 动态调用 `AnalyzerAgent` 选定的工具（MCP）来执行实际的数据抓取任务。

-   **`ControllerAgent` (控制器)**: 评估抓取任务是否成功。如果成功，它会将本次任务所使用的方法和工具记录下来，形成经验。当未来遇到相似请求时，`AnalyzerAgent` 可以直接利用这些成功经验，提高效率和准确性。

-   **`DiscoveryAgent` (探索者 - todo)**: 这是项目的未来方向。该 Agent 能够根据用户问题和现有代码模板，自动探索、生成并验证新的抓取方案。一旦成功，新方案将被保存为一个可复用的 MCP 工具，并纳入到框架的经验库中。

## 主要特色

-   **多智能体协作**: 模拟人类专家团队解决问题的流程，分工明确，逻辑清晰。
-   **动态工具选择**: 能根据任务动态选择最合适的执行方案，而非依赖固定的爬虫脚本。
-   **经验驱动学习**: 具备学习能力，能够从成功的任务中总结经验，持续提升自动化效率。
-   **API 驱动**: 通过 `FastAPI/Starlette` 提供服务，易于集成和二次开发。
-   **可扩展性**: 可以通过添加新的 MCP（Micro-Crawling Pattern）工具或实现 `DiscoveryAgent` 来不断扩展其能力。

## 安装指南

1.  **克隆项目**
    ```bash
    git clone xxx
    cd spiderme
    ```

2.  **安装依赖**
    项目根目录和 `main_datafetcher` 目录下均有依赖文件。请确保两者的依赖都已安装。
    ```bash
    # 安装主依赖
    pip install -r requirements.txt
    # 安装爬虫模块的依赖的浏览器组件等
    crawl4ai-setup
    #验证
    crawl4ai-doctor

    # 安装数据获取模块的依赖
    pip install -r main_datafetcher/requirements.txt
    ```
    *注意：建议使用虚拟环境（如 venv 或 conda）来管理项目依赖。*

3.  **配置环境变量**
    项目中可能包含需要配置 API 密钥或其他敏感信息的部分。请检查 `main_datafetcher/env_template` 文件，并创建相应的 `.env` 文件填入配置。

## 如何使用

1.  **启动 MCP 解决方案服务器**
    根据您的配置，首先启动 `mcp_servers` 目录中定义的微抓取解决方案服务。
    ```bash
    # 示例：启动一个PDF处理服务
    cd mcp_servers
    python mcp_href_pdf.py
    ```

2.  **启动主 Agent 服务**
    `main_datafetcher/main_api.py` 是项目的主入口。运行以下命令启动 Agent API 服务：
    ```bash
    cd main_datafetcher
    python main_api.py
    ```
    -   `--host`: 服务器绑定的主机名，可指定为 `0.0.0.0` 以允许外部访问。
    -   `--port`: 服务器监听的端口号。

3.  **测试服务**
    服务启动后，您可以使用 `example/a2a_client.py` 或其他 HTTP 客户端（如 Postman, curl）来与 Agent API 进行交互，测试其功能是否正常。

## 路线图 (Roadmap)

-   [ ] **实现 `DiscoveryAgent`**: 完成其自动探索和生成新抓取方案的能力。
-   [ ] **完善经验数据库和其它存储数据库**: 优化 `ControllerAgent` 的经验存储和检索逻辑，存储经验结果。
-   [ ] **丰富 MCP 工具集**: 开发更多针对不同场景（如JavaScript渲染、验证码处理）的微抓取模式。
-   [ ] **添加Web UI**: 为框架提供一个用户友好的图形界面，方便监控和管理。
