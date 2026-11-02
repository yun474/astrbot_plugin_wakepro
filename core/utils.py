import re
from collections.abc import Iterable

from astrbot.core.message.components import At
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.star.filter.command import CommandFilter
from astrbot.core.star.filter.command_group import CommandGroupFilter
from astrbot.core.star.star_handler import star_handlers_registry

from .model import WakeContext

# 指令参数中可识别为用户 ID 的写法：123456、@123456、<@openid>
_TARGET_ID_PATTERN = re.compile(r"^(?:<@!?([A-Za-z0-9_-]+)>|@?([A-Za-z0-9_-]+))$")

# 机器人账号区间（闭区间）
BOT_RANGES: list[tuple[int, int]] = [
    (3328144510, 3328144510),
    (2854196301, 2854216399),
    (66600000, 66600000),
    (3889000000, 3889999999),
    (4010000000, 4019999999),
]


def is_qqbot(uid: int | str) -> bool:
    """
    判断是否为 qqbot 账号

    :param uid: 用户 ID（支持 str / int）
    :return: 是否为 bot
    """
    try:
        uid = int(uid)
    except (TypeError, ValueError):
        return False

    for start, end in BOT_RANGES:
        if start <= uid <= end:
            return True
    return False


def get_all_commands() -> list[str]:
    """遍历所有注册的处理器获取所有命令"""
    commands = []
    for handler in star_handlers_registry:
        for fl in handler.event_filters:
            if isinstance(fl, CommandFilter):
                commands.append(fl.command_name)
                break
            elif isinstance(fl, CommandGroupFilter):
                commands.append(fl.group_name)
                break
    return commands


def parse_user_id(text: str) -> str | None:
    """解析单个用户 ID 写法：123456、@123456、<@openid>，无法识别时返回 None"""
    match = _TARGET_ID_PATTERN.match(text.strip())
    return (match.group(1) or match.group(2)) if match else None


def get_target_ids(event: AstrMessageEvent) -> list[str]:
    """
    提取指令要操作的用户 ID 列表（去重、保持顺序）
    支持艾特、@123456、<@openid> 以及直接输入的 ID，排除 Bot 自身和全体成员
    """
    excluded = {"", "all", str(event.get_self_id())}
    ids = [str(seg.qq) for seg in event.get_messages() if isinstance(seg, At)]
    # 第一个词是指令名，其余为参数
    for token in event.message_str.split()[1:]:
        uid = parse_user_id(token)
        if uid:
            ids.append(uid)
    return [uid for uid in dict.fromkeys(ids) if uid not in excluded]


def has_prefix(ctx: WakeContext, prefixes: Iterable[str]) -> bool:
    """
    按 AstrBot 的规则判断消息是否以前缀开头：比对整条消息的文本，
    不受开头引用、艾特 Bot 等消息段影响；群聊中首段为艾特他人时不算前缀消息
    """
    if not ctx.plain:
        return False
    first = ctx.chain[0] if ctx.chain else None
    if ctx.gid and isinstance(first, At) and str(first.qq) not in (str(ctx.bid), "all"):
        return False
    return any(p and ctx.plain.startswith(p) for p in prefixes)
