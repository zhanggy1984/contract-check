"""应用配置：从环境变量 / backend/.env 读取。"""
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # LLM（DeepSeek）
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-chat"
    llm_timeout: int = 120          # LLM 抽取/语义调用超时（秒），与现状硬编码一致
    llm_max_retries: int = 3        # openai SDK 内置 429/5xx 指数退避重试次数

    # MySQL（本机 docker compose）
    mysql_host: str = "localhost"
    mysql_port: int = 3306
    mysql_database: str = "contract_check"
    mysql_user: str = "contract"
    mysql_password: str = "contract123"

    # 上传
    upload_dir: str = "data/uploads"
    max_upload_mb: int = 50

    # 任务执行超时（秒）：图执行超过该值标记 FAILED（软超时，后台线程继续跑真实结果）
    task_timeout_seconds: int = 600

    # 工具决策（function calling 决策引擎，见 app/graph/decisions.py）
    tool_decision_enabled: bool = True            # 决策引擎总开关（False = 零决策调用，回退旧行为）
    ocr_decision_enabled: bool = True             # OCR 决策点开关
    ocr_decision_allow_llm_skip: bool = False     # 保守：LLM skip 不生效，执行仍强制 OCR
    extract_decision_allow_llm_retry: bool = False  # 保守：LLM retry 不生效，执行仍 FAILED
    tool_decision_max_rounds: int = 2             # 单决策点最大工具轮次（防循环）
    tool_decision_max_tokens: int = 1024          # 决策调用 max_tokens（话术短，无需 8192）
    tool_decision_timeout: int = 30               # 决策调用超时（秒）

    # 说明：从 backend/ 目录启动 uvicorn，env_file 相对路径指向 backend/.env
    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
