"""4.3 标准契约清单端点（平台定标准，agent 适配）。

统一 `GET /api/contracts`（公开无鉴权），声明本 agent 的 LLM 评测接口、场景清单与
**驱动契约（contract 段，manifest v2）**。平台脚手架读此端点做接口自动发现（决策
#55/#56）与 adapter 生成（{{input.*}}/{{auth.*}}/{{prepare.*}} 占位符由平台渲染）。
llm=false 为辅助接口（上传等），只登记不进 agent_interface。contract 段是平台驱动本
agent 的权威声明，改动需与平台 seed 快照保持同构（discover 会对比漂移）。
"""
from fastapi import APIRouter

router = APIRouter(prefix="/contracts", tags=["contracts"])

MANIFEST = {
    "agent": "contract-check",
    "contract_version": "2.0",
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
    "contract": {
        "type": "sync", "timeout": 300,
        "prepare": [
            {"name": "upload", "method": "POST", "path": "/api/files/upload",
             "files": {"file": "{{input.file_path}}"},
             "extract": {"task_id": "task_id"}},
            # 决策 #41：不 resume，取 WAITING_REVIEW 时的 result 打分（不产生假 review 记录）
            {"name": "wait_done", "poll": {
                "path": "/api/tasks/{{prepare.upload.task_id}}",
                "until": {"status": ["WAITING_REVIEW", "SUCCESS", "FAILED", "CANCELLED"]},
                "interval": 2, "timeout": 300}},
        ],
        "request": {"path": "/api/tasks/{{prepare.upload.task_id}}/result", "method": "GET"},
    },
}


@router.get("", summary="标准契约清单")
async def contracts() -> dict:
    return MANIFEST
