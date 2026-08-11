"""应用配置：从环境变量 / backend/.env 读取。"""
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # LLM（DeepSeek）
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-chat"

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

    # 说明：从 backend/ 目录启动 uvicorn，env_file 相对路径指向 backend/.env
    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
