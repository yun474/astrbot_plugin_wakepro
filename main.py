import time

from astrbot.api.event import filter
from astrbot.api.star import Context, Star
from astrbot.core.config.astrbot_config import AstrBotConfig
from astrbot.core.message.components import Plain
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.star.filter.command import CommandFilter
from astrbot.core.star.filter.command_group import CommandGroupFilter
from astrbot.core.star.star import StarMetadata
from astrbot.core.star.star_handler import star_handlers_registry

from .core.config import PluginConfig
from .core.model import (
    MemberState,
    StateManager,
    WakeContext,
)
from .core.pipeline import Pipeline
from .core.utils import get_target_ids, parse_user_id


class WakePlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.cfg = PluginConfig(config, context)
        self._commands: dict[str, set[str]] | None = None
        self._commands_registry_size = 0
        self.pipeline = Pipeline(self.cfg)

    async def initialize(self) -> None:
        """Initialize the wake pipeline and its optional Jev client."""
        await self.pipeline.initialize()

    async def terminate(self) -> None:
        """Release resources held by the wake pipeline."""
        await self.pipeline.terminate()

    @staticmethod
    def _get_all_commands() -> dict[str, set[str]]:
        """Read command names and aliases from the current handler registry.

        Returns:
            Complete command names and aliases, indexed by their first token.
            Group paths are retained for matching; context uses the first token
            for the existing built-in command blocking rules.
        """
        commands: dict[str, set[str]] = {}
        for handler in star_handlers_registry:
            for fl in handler.event_filters:
                if isinstance(fl, (CommandFilter, CommandGroupFilter)):
                    for name in fl.get_complete_command_names():
                        commands.setdefault(name.split(" ", 1)[0], set()).add(name)
                    break
        return commands

    @filter.on_plugin_loaded()
    async def on_plugin_loaded(self, metadata: StarMetadata) -> None:
        """Invalidate the command cache after a plugin loads.

        Args:
            metadata: Metadata of the loaded plugin.
        """
        self._commands = None

    @filter.on_plugin_unloaded()
    async def on_plugin_unloaded(self, metadata: StarMetadata) -> None:
        """Invalidate the cache before the plugin's handlers are removed.

        Args:
            metadata: Metadata of the unloading plugin.
        """
        # Registry cleanup can happen after this hook; the size check below
        # also invalidates any cache rebuilt while unloading is still in progress.
        self._commands = None

    @filter.event_message_type(filter.EventMessageType.ALL, priority=99999)
    async def on_group_msg(self, event: AstrMessageEvent):
        """收到消息后"""
        umo = event.unified_msg_origin
        gid = event.get_group_id()
        uid = event.get_sender_id()
        bid = event.get_self_id()
        platform_id = str(event.get_platform_id() or "")

        if uid == bid:
            return

        chain = event.get_messages()
        plains = [seg.text for seg in chain if isinstance(seg, Plain)]
        plain = " ".join(plains).strip()
        # AstrBot has already removed the wake prefix before dispatching handlers.
        message_str = " ".join(event.message_str.split())
        if self._commands is None or self._commands_registry_size != len(
            star_handlers_registry
        ):
            self._commands = self._get_all_commands()
            self._commands_registry_size = len(star_handlers_registry)
        first_arg = message_str.split(" ", 1)[0]
        # Match full paths, but keep the root token for built-in blocking.
        cmd = (
            first_arg
            if any(
                message_str == name or message_str.startswith(f"{name} ")
                for name in self._commands.get(first_arg, ())
            )
            else None
        )

        group = (
            StateManager.get_group(gid, platform_id=platform_id, bot_id=str(bid))
            if gid
            else None
        )
        if group and uid not in group.members:
            group.members[uid] = MemberState(uid=uid)
        if group and not cmd:
            message_id = str(getattr(event.message_obj, "message_id", "") or "")
            group.history.append(
                {
                    "message_id": message_id or f"{uid}:{time.time_ns()}",
                    "sender_id": str(uid),
                    "sender_name": event.get_sender_name() or "",
                    "text": plain,
                    "time": time.time(),
                    "is_bot": False,
                    "bot_id": str(bid),
                }
            )

        ctx = WakeContext(
            event=event,
            chain=chain,
            plain=plain,
            cmd=cmd,
            is_admin=event.is_admin(),
            umo=umo,
            gid=gid,
            uid=uid,
            bid=bid,
            group=group,
            member=group.members[uid] if group else None,
            now=time.time(),
        )
        await self.pipeline.run(ctx)

    @filter.on_decorating_result(priority=20)
    async def on_decorating_result(self, event: AstrMessageEvent):
        """消息发送前"""
        uid = event.get_sender_id()
        if uid:
            await StateManager.clear_pending_request(
                StateManager.get_pending_key(event.unified_msg_origin, uid),
                event=event,
            )

        gid: str = event.get_group_id()
        result = event.get_result()

        if not gid or not uid or not result:
            return

        group = StateManager.get_group(
            gid,
            platform_id=str(event.get_platform_id() or ""),
            bot_id=str(event.get_self_id()),
        )
        group.bot_msgs.append(result.get_plain_text())
        group.history.append(
            {
                "message_id": f"bot:{event.get_self_id()}:{time.time_ns()}",
                "sender_id": str(event.get_self_id()),
                "sender_name": "",
                "text": result.get_plain_text(),
                "time": time.time(),
                "is_bot": True,
                "bot_id": str(event.get_self_id()),
            }
        )

        member = group.members.get(uid)
        if (
            member
            and member.can_prolong
            and event.get_extra("wakepro_prolong_reply", False)
        ):
            member.last_reply = time.time()

    @filter.llm_tool()
    async def llm_set_wake_block(
        self, event: AstrMessageEvent, user_id: str, blocked: bool
    ) -> str:
        """屏蔽或解除屏蔽指定用户，屏蔽时用户的消息将被屏蔽，不会被回复。仅管理员发起时可用。
        Args:
            user_id(string): 要操作的用户 ID，例如 QQ 号。
            blocked(boolean): true 表示屏蔽该用户（默认为true），false 表示解除屏蔽。
        """
        # 只接受管理员发起的操作，避免普通成员借助 LLM 屏蔽他人
        if not event.is_admin():
            return "只有管理员可以屏蔽或解除屏蔽用户"
        uid = parse_user_id(user_id)
        if not uid:
            return f"无法识别的用户 ID：{user_id}"
        if blocked:
            if uid in self._get_admin_ids(event):
                return f"不能屏蔽管理员：{uid}"
            if self.cfg.add_block_user(uid):
                return f"已屏蔽用户：{uid}"
            return f"用户屏蔽中：{uid}"
        if self.cfg.remove_block_user(uid):
            return f"已解除屏蔽：{uid}"
        return f"用户未被屏蔽：{uid}"

    def _get_admin_ids(self, event: AstrMessageEvent) -> set[str]:
        """读取当前会话所用配置文件中的管理员 ID"""
        admins = self.context.get_config(umo=event.unified_msg_origin).get(
            "admins_id", []
        )
        return {str(admin_id) for admin_id in admins}

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("屏蔽用户", alias={"屏蔽唤醒"})
    async def cmd_block_wake(self, event: AstrMessageEvent, target_id: str = ""):
        """屏蔽用户 @用户A @用户B 或用户ID（仅管理员）"""
        uids = get_target_ids(event)
        if not uids:
            blocked_users = self.cfg.block.block_users
            msg = (
                "当前屏蔽的用户：\n" + "\n".join(blocked_users)
                if blocked_users
                else "当前没有屏蔽任何用户，请用命令“屏蔽用户 @用户”添加"
            )
            yield event.plain_result(msg)
            return
        admin_ids = self._get_admin_ids(event)
        results = []
        for uid in uids:
            if uid in admin_ids:
                results.append(f"不能屏蔽管理员：{uid}")
            elif self.cfg.add_block_user(uid):
                results.append(f"已屏蔽用户：{uid}")
            else:
                results.append(f"用户屏蔽中：{uid}")
        yield event.plain_result("\n".join(results))

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("解除屏蔽")
    async def cmd_allow_wake(self, event: AstrMessageEvent, target_id: str = ""):
        """解除屏蔽 @用户A @用户B 或用户ID（仅管理员）"""
        uids = get_target_ids(event)
        if not uids:
            blocked_users = self.cfg.block.block_users
            yield event.plain_result(
                "当前可解除屏蔽的用户：\n" + "\n".join(blocked_users)
                if blocked_users
                else "当前没有屏蔽任何用户"
            )
            return
        results = []
        for uid in uids:
            if self.cfg.remove_block_user(uid):
                results.append(f"已解除屏蔽：{uid}")
            else:
                results.append(f"用户未被屏蔽：{uid}")
        yield event.plain_result("\n".join(results))
