from ..config import PluginConfig
from ..model import StepName, StepResult, WakeContext, WakeType
from ..utils import has_prefix
from .base import BaseStep


class ProlongStep(BaseStep):
    """Keep an eligible conversation awake after the bot's last reply."""

    name = StepName.PROLONG

    def __init__(self, config: PluginConfig):
        super().__init__(config)
        self.cfg = config.prolong

    async def handle(self, ctx: WakeContext) -> StepResult:
        """Wake a follow-up message while its conversation window is open.

        Args:
            ctx: Current message context and conversation state.

        Returns:
            A wake result when the member is eligible for wake extension.
        """
        if self.cfg.seconds <= 0 or not ctx.member:
            return StepResult()
        # Silence remains enforced by WakeStep; do not open a window before it.
        if (ctx.group and ctx.group.shutup_until > ctx.now) or (
            ctx.member.silence_until > ctx.now
        ):
            return StepResult()
        # Core prefix wakes still count when command filtering is skipped.
        if ctx.event.is_at_or_wake_command and has_prefix(
            ctx, self.config.get_wake_prefix(ctx.umo)
        ):
            ctx.wake_types.add(WakeType.PREFIX)
        if ctx.wake_types & self.cfg._activation_types:
            ctx.member.can_prolong = True
            ctx.member.prolong_count = 0
            ctx.member.last_reply = 0.0
            ctx.event.set_extra("wakepro_prolong_reply", True)
            return StepResult()
        if ctx.debounce_follow_up or ctx.cmd or ctx.event.is_at_or_wake_command:
            return StepResult()
        if not ctx.member.can_prolong or not ctx.member.last_reply:
            return StepResult()
        if ctx.now - ctx.member.last_reply > self.cfg.seconds:
            ctx.member.can_prolong = False
            return StepResult()
        if self.cfg.max_count > 0 and ctx.member.prolong_count >= self.cfg.max_count:
            return StepResult()
        return StepResult(wake=True, wake_type=WakeType.PROLONG, msg="Wake extension")
