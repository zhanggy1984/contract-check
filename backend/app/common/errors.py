"""任务取消短路异常：图节点入口检测到 CANCELLED 时抛出，服务层据此置 CANCELLED（而非 FAILED）。"""


class TaskCancelledError(RuntimeError):
    pass
