from astrbot.api import logger

from ..config import PluginConfig
from ..model import StepName, StepResult, WakeContext
from ..utils import has_prefix
from .base import BaseStep


class PrefixStep(BaseStep):
    """前缀拦截：禁止使用前缀唤醒 LLM，指令不受影响。

    独立于指令屏蔽，白名单可只跳过指令屏蔽而不跳过本步骤，
    使管理员能使用指令但仍无法通过前缀唤醒 LLM。
    """

    name = StepName.PREFIX

    def __init__(self, config: PluginConfig):
        super().__init__(config)
        self.cfg = config.command
        if self.cfg.block_prefix_llm and not config.pipeline.is_enabled_step(self.name):
            logger.warning(
                "已开启“禁止使用前缀唤醒LLM”，但“启用的步骤”中未勾选 prefix(前缀拦截)，前缀拦截不会生效"
            )

    async def handle(self, ctx: WakeContext) -> StepResult:
        # 优先使用前缀拦截列表，未配置时使用会话的唤醒前缀
        if (
            not self.cfg.block_prefix_llm
            or ctx.cmd
            or not has_prefix(ctx, self.config.get_llm_block_prefixes(ctx.umo))
        ):
            return StepResult()
        if ctx.event.is_at_or_wake_command:
            return StepResult(wake=False, abort=True, msg="前缀触发的LLM已被禁用")
        # 尚未被唤醒时只终止后续唤醒判定，不停止事件，避免影响其他插件
        return StepResult(abort=True, msg="前缀消息，已跳过后续唤醒判定")
