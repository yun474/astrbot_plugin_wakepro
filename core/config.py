# config.py
from __future__ import annotations

import json
from collections.abc import Mapping, MutableMapping
from pathlib import Path
from types import MappingProxyType, UnionType
from typing import Any, Union, get_args, get_origin, get_type_hints

from astrbot.api import logger
from astrbot.core.config.astrbot_config import AstrBotConfig
from astrbot.core.star.context import Context
from astrbot.core.utils.astrbot_path import get_astrbot_plugin_path

from .model import WakeType


class ConfigNode:
    _SCHEMA_CACHE: dict[type, dict[str, type]] = {}
    _FIELDS_CACHE: dict[type, set[str]] = {}

    @classmethod
    def _schema(cls) -> dict[str, type]:
        return cls._SCHEMA_CACHE.setdefault(cls, get_type_hints(cls))

    @classmethod
    def _fields(cls) -> set[str]:
        return cls._FIELDS_CACHE.setdefault(
            cls,
            {k for k in cls._schema() if not k.startswith("_")},
        )

    @staticmethod
    def _is_optional(tp: type) -> bool:
        if get_origin(tp) in (Union, UnionType):
            return type(None) in get_args(tp)
        return False

    def __init__(self, data: MutableMapping[str, Any]):
        object.__setattr__(self, "_data", data)
        object.__setattr__(self, "_children", {})
        for key, tp in self._schema().items():
            if key.startswith("_"):
                continue
            if key in data:
                continue
            if hasattr(self.__class__, key):
                continue
            if self._is_optional(tp):
                continue
            logger.warning(f"[config:{self.__class__.__name__}] miss key: {key}")

    def __getattr__(self, key: str) -> Any:
        if key in self._fields():
            value = self._data.get(key)
            tp = self._schema().get(key)

            if isinstance(tp, type) and issubclass(tp, ConfigNode):
                children: dict[str, ConfigNode] = self.__dict__["_children"]
                if key not in children:
                    if not isinstance(value, MutableMapping):
                        raise TypeError(
                            f"[config:{self.__class__.__name__}] "
                            f"key {key} need dict but {type(value).__name__}"
                        )
                    children[key] = tp(value)
                return children[key]

            return value

        if key in self.__dict__:
            return self.__dict__[key]

        raise AttributeError(key)

    def __setattr__(self, key: str, value: Any) -> None:
        if key in self._fields():
            self._data[key] = value
            return
        object.__setattr__(self, key, value)

    def raw_data(self) -> Mapping[str, Any]:
        return MappingProxyType(self._data)

    def save_config(self) -> None:
        if not isinstance(self._data, AstrBotConfig):
            raise RuntimeError(
                f"{self.__class__.__name__}.save_config() only supports AstrBotConfig"
            )
        self._data.save_config()


class PipelineConfig(ConfigNode):
    steps: list[str]
    whitelist: list[str]
    whitelist_steps: list[str]
    blacklist: list[str]
    blacklist_steps: list[str]

    def __init__(self, data: MutableMapping[str, Any]):
        super().__init__(data)
        self._steps = self._parse_steps(self.steps)
        self._whitelist_steps = self._parse_steps(self.whitelist_steps)
        self._blacklist_steps = self._parse_steps(self.blacklist_steps)

    @staticmethod
    def _parse_steps(steps: list[str]) -> set[str]:
        return {name.split("(", 1)[0].strip() for name in steps if name.strip()}

    @staticmethod
    def _contains_target(targets: list[str], *values: str) -> bool:
        return any(value and value in targets for value in values)

    def is_enabled_step(self, step_name: str) -> bool:
        return step_name in self._steps

    def in_whitelist(self, step_name: str, *values: str) -> bool:
        return step_name in self._whitelist_steps and self._contains_target(
            self.whitelist, *values
        )

    def in_blacklist(self, step_name: str, *values: str) -> bool:
        return step_name in self._blacklist_steps and self._contains_target(
            self.blacklist, *values
        )


class BlockConfig(ConfigNode):
    wake_cd: float
    block_qqbot: bool
    reread: bool
    keywords: list[str]
    block_users: list[str]

    def __init__(self, data: MutableMapping[str, Any]):
        super().__init__(data)
        self.block_users = [
            str(uid).strip() for uid in (self.block_users or []) if str(uid).strip()
        ]


class CommandConfig(ConfigNode):
    builtin_cmds: list[str]
    block_builtin: bool
    block_prefix_cmd: bool
    block_prefix_llm: bool
    block_llm_prefixes: list[str]


class MentionConfig(ConfigNode):
    names: list[str]
    admin_names: list[str]
    disable_reply_wake: bool
    disable_reply_other_wake: bool
    disable_at_other_wake: bool


class ProlongConfig(ConfigNode):
    activation: list[str]
    seconds: float
    max_count: int

    def __init__(self, data: MutableMapping[str, Any]):
        super().__init__(data)
        self._activation_types = {
            WakeType(name.split("(", 1)[0].strip())
            for name in self.activation
            if name.strip()
        }


class WakeConfig(ConfigNode):
    similar: float
    ask: float
    bored: float
    interest_words_str: list[str]
    interest: float
    prob: float

    def __init__(self, data: MutableMapping[str, Any]):
        super().__init__(data)
        self._interest_words = [w.split() for w in self.interest_words_str]


class JevConfig(ConfigNode):
    jev_api_key: str
    jev_api_url: str
    jev_model: str
    jev_threshold: float
    jev_timeout_seconds: float
    jev_retries: int
    history_messages: int


class SilenceConfig(ConfigNode):
    shutup: float
    insult: float
    ai: float
    multiple: float


class DebounceConfig(ConfigNode):
    listen_seconds: float
    max_merge_count: int
    message_types: list[str]

    def __init__(self, data: MutableMapping[str, Any]):
        super().__init__(data)
        self._message_types = {
            name.split("(", 1)[0].strip() for name in self.message_types if name.strip()
        }


class PluginConfig(ConfigNode):
    pipeline: PipelineConfig
    command: CommandConfig
    block: BlockConfig
    mention: MentionConfig
    prolong: ProlongConfig
    wake: WakeConfig
    jev: JevConfig
    debounce: DebounceConfig
    silence: SilenceConfig

    _plugin_name: str = "astrbot_plugin_wakepro"

    def __init__(self, config: AstrBotConfig, context: Context):
        super().__init__(config)
        self.context = context
        self.admins_id: list[str] = context.get_config().get("admins_id", [])
        self.plugin_dir = Path(get_astrbot_plugin_path()) / self._plugin_name
        self.block_words_file = self.plugin_dir / "block_words.json"
        self._normalize_whitelist()
        self._normalize_block_words()

    def get_wake_prefix(self, umo: str) -> list[str]:
        """读取会话所用配置文件的唤醒前缀，不同会话可能绑定不同的配置文件"""
        prefixes = self.context.get_config(umo=umo).get("wake_prefix", [])
        return [p for p in prefixes if p]

    def get_llm_block_prefixes(self, umo: str) -> list[str]:
        """前缀拦截列表非空时只用该列表，否则回退到会话的唤醒前缀"""
        custom = [p for p in self.command.block_llm_prefixes if p]
        return custom or self.get_wake_prefix(umo)

    def _normalize_whitelist(self):
        if not self.admins_id:
            return
        changed = False
        for admin_id in self.admins_id:
            if admin_id not in self.pipeline.whitelist:
                self.pipeline.whitelist.append(admin_id)
                changed = True
        if changed:
            self.save_config()

    def _normalize_block_words(self):
        if not self.block.keywords:
            if self.block_words_file.exists():
                with open(self.block_words_file, encoding="utf-8") as f:
                    self.block.keywords = json.load(f)
                self.save_config()

    def add_block_user(self, user_id: str | int) -> bool:
        uid = str(user_id).strip()
        if not uid or uid in self.block.block_users:
            return False
        self.block.block_users.append(uid)
        self.save_config()
        return True

    def remove_block_user(self, user_id: str | int) -> bool:
        uid = str(user_id).strip()
        if not uid or uid not in self.block.block_users:
            return False
        self.block.block_users.remove(uid)
        self.save_config()
        return True
