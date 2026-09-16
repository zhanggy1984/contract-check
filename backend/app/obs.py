"""观测边带（§11.3 cc 接入）：obs_sdk 惰性门 + request/LLM 打点统一收口。

为什么集中在这一个模块（对齐 cs/gq 的接入语义，但 cc 消费点更多——request 中间件、
后台任务合成 span、两个 LLM chokepoint——故把"取 sdk 模块"的门收敛为单点）：
- 观测不阻塞业务：三要素（obs_enabled/kafka_servers/topic）缺一或包缺失 → obs() 返 None，
  所有打点包装空转零开销，业务路径零改动语义。
- 单 monkeypatch seam：各处只 import 本模块函数，测试替 fake 只需替换 app.obs.obs。
- record_llm 强依赖 request span（sdk _require_span 无 span 事件不产），LLM 打点合法性
  由 begin/end 的 request 生命周期保证（合成 task span 见 check_task_service）。

打点口径（**LLM 级 error_type 是平台错误分类白名单值域，非自由字符串**；白名单外的值
平台不产生回流候选，故 LLM 级一律经 llm_error_type 折叠；request 级仍是自由字符串）：
- request 级：HTTP 状态码 >=400 → HTTP_{code}；后台任务超时/取消/异常 → TIMEOUT/CANCELLED/INTERNAL_ERROR。
- LLM 级：仅 429 单列（llm_rate_limit），其余状态码/类名在白名单无对应词统一归 llm_other（见 llm_error_type）。
"""
import logging
import time

from app.config import settings

logger = logging.getLogger(__name__)


def obs():
    """惰性取 obs_sdk 模块：未启用（config 三要素缺一）或未安装时返回 None。"""
    if not settings.obs_ready:
        return None
    try:
        import obs_sdk
    except ImportError:
        logger.warning("[obs] obs_sdk 未安装，观测边带关闭（OBS_ENABLED=true 但包缺失）")
        return None
    return obs_sdk


# ---------- init / shutdown（main.py startup/shutdown 钩子调用） ----------


def init(agent: str) -> None:
    """装配 obs_sdk（观测边带）：失败仅告警不拦启动。agent = 消费白名单名（全名）。"""
    mod = obs()
    if mod is None:
        return
    try:
        mod.init(
            agent,
            kafka_servers=settings.obs_kafka_servers,
            topic=settings.obs_kafka_topic,
            sasl_username=settings.obs_kafka_sasl_username or None,
            sasl_password=settings.obs_kafka_sasl_password or None,
            flush_batch=settings.obs_flush_batch,
            flush_interval_s=settings.obs_flush_interval_s,
            log_mode="stdlib",  # cc 命名 logger 全部传播到 root，挂 root 即全覆盖
        )
        logger.info("[obs] obs_sdk 已初始化 topic=%s", settings.obs_kafka_topic)
    except Exception as e:  # 观测边带故障不拦服务启动
        logger.warning("[obs] obs_sdk init 失败（观测边带关闭）: %s", e)


def shutdown() -> None:
    """收尾：终刷剩余事件后关线程（幂等：未 init 也安全）。"""
    mod = obs()
    if mod is None:
        return
    try:
        mod.shutdown()
    except Exception as e:
        logger.warning("[obs] obs_sdk shutdown 异常: %s", e)


# ---------- request 出入口（中间件 / 后台任务合成 span 共用） ----------


def begin_request(*, method: str = "GET", path: str = "/",
                  trace_id: str | None = None) -> bool:
    """request 入口。返回是否真正建了 span（供调用方决定是否配对 end_request）。"""
    mod = obs()
    if mod is None:
        return False
    try:
        mod.begin_request(method=method, path=path, trace_id=trace_id)
        return True
    except Exception:
        logger.debug("[obs] begin_request 异常", exc_info=True)
        return False


def end_request(status: str, *, error_type: str | None = None,
                error_msg: str | None = None, input: object = None) -> None:
    """request 出口。status 合法性/补 duration 由 sdk 自判，此处不重复。

    input 是该请求入参：sdk 侧「不传则事件不带该键」，而平台侧 root_input_hash 与
    case 现场**只认这个键**——缺则 trace_judge_state.root_input_hash 恒 NULL，
    cluster_job 按「残 trace 无 input 现场」（Fork A）只置 processed 不建簇，
    回流闭环直接断在环③。故各出口都要给得出入参；合成 span 的真实入参即 task_id。
    """
    mod = obs()
    if mod is None:
        return
    try:
        mod.end_request(status, error_type=error_type, error_msg=error_msg, input=input)
    except Exception:
        logger.debug("[obs] end_request 异常", exc_info=True)


# ---------- LLM 出口（llm_client.call_json / tool_client.call_with_tools 打点） ----------


def llm_start() -> float:
    """LLM 调用计时起点（观测未启用时开销仅一次 monotonic）。"""
    return time.monotonic()


def record_llm_ok(started: float, usage: dict | None) -> None:
    """LLM 成功打点（usage 原样透传供消费端落 token 指标；观测故障不抛）。"""
    mod = obs()
    if mod is None:
        return
    try:
        mod.record_llm(settings.deepseek_model, "ok",
                       duration_ms=_duration_since(started), usage=usage)
    except Exception:
        logger.debug("[obs] record_llm ok 打点异常", exc_info=True)


def record_llm_error(started: float, exc: BaseException) -> None:
    """LLM 失败打点。「先记 error 再抛」由 chokepoint 保证（§2.4 前提）。"""
    mod = obs()
    if mod is None:
        return
    try:
        mod.record_llm(settings.deepseek_model, "error",
                       duration_ms=_duration_since(started),
                       error_type=llm_error_type(exc),
                       error_msg=str(exc)[:512])
    except Exception:
        logger.debug("[obs] record_llm error 打点异常", exc_info=True)


def llm_error_type(exc: BaseException) -> str:
    """LLM 异常 → error_type（平台错误分类白名单值域，**非自由字符串**，口径对齐 cs）。

    仅 429 单列（白名单 llm_rate_limit）；其余状态码（含 401/403 auth 类）与类名在白名单
    无对应词，统一归 llm_other——原始信息由 error_msg 保留。
    """
    code = getattr(exc, "status_code", None)
    if isinstance(code, int):
        return "llm_rate_limit" if code == 429 else "llm_other"
    name = type(exc).__name__.lower()
    if "timeout" in name:
        return "llm_timeout"
    if "connection" in name or "connect" in name:
        return "llm_connection"
    if "ratelimit" in name or "rate" in name:
        return "llm_rate_limit"
    return "llm_other"


def _duration_since(started: float) -> int:
    return max(0, int((time.monotonic() - started) * 1000))
