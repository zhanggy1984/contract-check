"""4.3 标准契约清单端点（平台定标准，agent 适配）。

统一 `GET /api/contracts`（公开无鉴权），声明本 agent 的 LLM 评测接口与场景清单。
平台脚手架读此端点做接口自动发现（决策 #55/#56）。llm=false 为辅助接口（上传等），
只登记不进 agent_interface。interfaces[].path 为业务路径，与平台 seed_data 一致。
"""
from fastapi import APIRouter

router = APIRouter(prefix="/contracts", tags=["contracts"])

MANIFEST = {
    "agent": "contract-check",
    "contract_version": "1.0",
    "interfaces": [
        {"name": "result", "path": "/api/tasks/{task_id}/result", "method": "GET",
         "contract_type": "sync", "llm": True,
         "description": "合同校验结果（同步 JSON，透出 answer/usage/timing/tool_calls）"},
        {"name": "upload", "path": "/api/files/upload", "method": "POST",
         "llm": False, "description": "上传合同文件（辅助接口）"},
    ],
    "scenes": [
        {"tag": "missing_date", "description": "缺失生效日期"},
        {"tag": "single_party", "description": "单方签署"},
        {"tag": "scanned_pdf", "description": "扫描件识别"},
        {"tag": "conflict", "description": "条款冲突"},
        {"tag": "genuine", "description": "合规合同"},
    ],
}


@router.get("", summary="标准契约清单")
async def contracts() -> dict:
    return MANIFEST
