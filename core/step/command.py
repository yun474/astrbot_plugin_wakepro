from ..config import PluginConfig
from ..model import StepName, StepResult, WakeContext, WakeType
from ..utils import has_prefix
from .base import BaseStep


class CommandStep(BaseStep):
    name = StepName.COMMAND

    def __init__(self, config: PluginConfig):
        super().__init__(config)
        self.cfg = config.command

    async def handle(self, ctx: WakeContext) -> StepResult:
        if ctx.debounce_follow_up:
            return StepResult(msg="消息防抖窗口内，跳过指令判定")

        if self.cfg.block_builtin and ctx.cmd and ctx.cmd in self.cfg.builtin_cmds:
            return StepResult(wake=False, abort=True, msg=f"命令 '{ctx.cmd}' 已被禁用")

        # 屏蔽前缀触发LLM：优先使用前缀拦截列表，未配置时使用会话的唤醒前缀
        if (
            self.cfg.block_prefix_llm
            and not ctx.cmd
            and has_prefix(ctx, self.config.get_llm_block_prefixes(ctx.umo))
        ):
            if ctx.event.is_at_or_wake_command:
                return StepResult(wake=False, abort=True, msg="前缀触发的LLM已被禁用")
            # 尚未被唤醒时只终止后续唤醒判定，不停止事件，避免影响其他插件
            return StepResult(abort=True, msg="前缀消息，已跳过后续唤醒判定")

        if has_prefix(ctx, self.config.get_wake_prefix(ctx.umo)):
            # 屏蔽前缀触发指令
            if ctx.cmd and self.cfg.block_prefix_cmd:
                return StepResult(
                    wake=False, abort=True, msg=f"前缀触发的指令 '{ctx.cmd}' 已被禁用"
                )
            return StepResult(wake=True, wake_type=WakeType.PREFIX, msg="Prefix wake")

        return StepResult()
