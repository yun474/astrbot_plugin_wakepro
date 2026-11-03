from __future__ import annotations

from astrbot.api import logger

from .config import PluginConfig
from .model import WakeContext, WakeType
from .step import (
    BaseStep,
    BlockStep,
    CommandStep,
    DebounceStep,
    JevStep,
    MentionStep,
    PrefixStep,
    ProlongStep,
    SilenceStep,
    WakeStep,
)


class Pipeline:
    """
    生产级 Pipeline：

    - 构建 step 实例
    - 统一 initialize / terminate
    - 对外唯一接口：run
    """

    STEP_REGISTRY: list[type[BaseStep]] = [
        BlockStep,
        PrefixStep,
        CommandStep,
        DebounceStep,
        MentionStep,
        ProlongStep,
        WakeStep,
        JevStep,
        SilenceStep,
    ]

    def __init__(self, config: PluginConfig):
        self.plugin_config = config
        self._steps: list[BaseStep] = []
        self._debounce_step: DebounceStep | None = None
        self._build_steps()

    def _build_steps(self) -> None:
        for cls in self.STEP_REGISTRY:
            step = cls(self.plugin_config)
            if isinstance(step, DebounceStep):
                self._debounce_step = step
            self._steps.append(step)

    # ==================== run =====================

    async def run(self, ctx: WakeContext):
        woke = False
        for step in self._steps:
            if not self.plugin_config.pipeline.is_enabled_step(step.name):
                continue
            if self.plugin_config.pipeline.in_whitelist(
                step.name, ctx.umo, ctx.uid, ctx.gid
            ):
                logger.debug(f"步骤 {step.name} 被白名单跳过")
                continue
            if self.plugin_config.pipeline.in_blacklist(
                step.name, ctx.umo, ctx.uid, ctx.gid
            ):
                logger.debug(f"步骤 {step.name} 被黑名单跳过")
                continue
            # 执行
            result = await step.handle(ctx)
            # 标记唤醒
            if result.wake is True:
                woke = True
                if result.wake_type:
                    ctx.wake_types.add(result.wake_type)
                if ctx.member:
                    ctx.member.last_wake = ctx.now
                    if result.wake_type == WakeType.PROLONG:
                        ctx.member.prolong_count += 1
                        ctx.event.set_extra("wakepro_prolong_reply", True)
                ctx.event.is_at_or_wake_command = True
            # 停止事件传播
            elif result.wake is False:
                woke = False
                if ctx.member:
                    ctx.member.can_prolong = False
                    ctx.member.prolong_count = 0
                ctx.event.set_extra("wakepro_prolong_reply", False)
                ctx.event.stop_event()
            # 日志
            if result.msg:
                logger.debug(result.msg)
            # 中断流水线
            if result.abort:
                break
        # Register after debounce has claimed the previous request and activation
        # has marked whether the eventual reply can refresh the extension window.
        if woke and not ctx.event.is_stopped() and self._debounce_step:
            await self._debounce_step.activate_window(ctx)

    async def initialize(self) -> None:
        """Initialize pipeline steps that own external resources."""
        for step in self._steps:
            await step.initialize()

    async def terminate(self) -> None:
        """Release resources owned by pipeline steps."""
        for step in reversed(self._steps):
            await step.terminate()
