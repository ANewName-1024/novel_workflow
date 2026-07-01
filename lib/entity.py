"""
Entity domain models: Character / Event / Foreshadow / WorldRule.

统一 4 实体的数据模型 + 序列化层。供:
  - memory.py: 持久化 (JSON 文件)
  - review_ui: REST API
  - extract.py: LLM 输出解析
  - 自检/评审: 一致性扫描

设计原则:
  - 全部用 dataclass, 单一职责, 默认值友好
  - from_dict() 容错 (旧字段缺/多/类型不对都不炸)
  - WorldRule 是新增实体 (v1.2 M1.1), 修仙/科幻/奇幻通用
"""
from __future__ import annotations

import datetime
import uuid
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any


# ── 类型枚举 ──────────────────────────────────────────────────────────────

class EntityType(str, Enum):
    CHARACTER = "character"
    EVENT = "event"
    FORESHADOW = "foreshadow"
    WORLD_RULE = "world_rule"


class WorldRuleCategory(str, Enum):
    SYSTEM = "体系"      # 灵根等级/魔法体系/修炼境界
    GEOGRAPHY = "地理"   # 地理版图/位面法则
    HISTORY = "历史"     # 历史背景/纪元
    RELIGION = "宗教"    # 神祇/教派/信仰
    TECHNOLOGY = "科技"  # 黑科技/超光速/能源
    MAGIC = "魔法"       # 元素相克/法术体系
    POLITICS = "政治"    # 王权/势力/阶级
    OTHER = "其他"


class WorldRuleStatus(str, Enum):
    DRAFT = "草案"
    ESTABLISHED = "已确立"
    ABANDONED = "已废弃"


class ForeshadowStatus(str, Enum):
    PLANTED = "已埋"
    PROGRESS = "推进中"
    RESOLVED = "已回收"
    ABANDONED = "已放弃"


# ── 工具 ──────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    """生成 ISO 时间戳 (秒精度), 跨时区安全."""
    return datetime.datetime.now().isoformat(timespec="seconds")


def gen_id(prefix: str) -> str:
    """生成短唯一 ID, 如 'rule_x7q2a9'."""
    return f"{prefix}_{uuid.uuid4().hex[:6]}"


# ── Character ─────────────────────────────────────────────────────────────

@dataclass
class Character:
    """角色实体."""

    name: str
    role: str = "配角"                            # 主角/配角/反派/路人
    traits: str = ""                              # 性格特征 (短句)
    appearance: str = ""                          # 外貌 (若有)
    importance: str = "中"                        # 高/中/低
    first_appearance: int | None = None           # 首次登场章节号
    relationship: str = ""                        # 当前关系网快照
    arc: str = ""                                 # 角色弧光 (起点 → 终点)
    aliases: list[str] = field(default_factory=list)   # 别名/绰号
    notes: str = ""
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)

    def touch(self) -> None:
        """更新 updated_at."""
        self.updated_at = _now_iso()

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Character":
        valid_keys = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in d.items() if k in valid_keys}
        if "name" not in filtered or not filtered["name"]:
            raise ValueError("Character.name 不能为空")
        if "created_at" not in filtered:
            filtered["created_at"] = _now_iso()
        if "updated_at" not in filtered:
            filtered["updated_at"] = filtered["created_at"]
        return cls(**filtered)


# ── Event ─────────────────────────────────────────────────────────────────

@dataclass
class Event:
    """事件实体."""

    event: str                                    # 事件简述 (20字内)
    significance: str = ""                        # 对主线的影响
    consequences: str = ""                        # 后续可能影响
    chapter: int | None = None                    # 发生章节号
    participants: list[str] = field(default_factory=list)   # 参与角色名
    notes: str = ""
    extracted_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Event":
        valid_keys = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in d.items() if k in valid_keys}
        if "event" not in filtered or not filtered["event"]:
            raise ValueError("Event.event 不能为空")
        if "extracted_at" not in filtered:
            filtered["extracted_at"] = _now_iso()
        return cls(**filtered)


# ── Foreshadow ────────────────────────────────────────────────────────────

@dataclass
class Foreshadow:
    """伏笔实体."""

    foreshadow: str                               # 伏笔内容 (15字内)
    significance: str = ""                        # 重要性
    hints: str = ""                               # 本章中出现的暗示位置/措辞
    status: str = ForeshadowStatus.PLANTED.value
    planted_chapter: int | None = None
    resolved_chapter: int | None = None
    resolved_at: str | None = None
    related_entities: list[str] = field(default_factory=list)  # 关联 ID
    notes: str = ""
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)

    def touch(self) -> None:
        self.updated_at = _now_iso()

    def mark_resolved(self, chapter: int | None = None) -> None:
        """标记为已回收."""
        self.status = ForeshadowStatus.RESOLVED.value
        self.resolved_at = _now_iso()
        if chapter is not None:
            self.resolved_chapter = chapter
        self.touch()

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Foreshadow":
        valid_keys = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in d.items() if k in valid_keys}
        if "foreshadow" not in filtered or not filtered["foreshadow"]:
            raise ValueError("Foreshadow.foreshadow 不能为空")
        # legacy: 旧数据 status 可能是缺失的
        if "status" not in filtered or not filtered["status"]:
            filtered["status"] = ForeshadowStatus.PLANTED.value
        if "created_at" not in filtered:
            filtered["created_at"] = _now_iso()
        if "updated_at" not in filtered:
            filtered["updated_at"] = filtered["created_at"]
        return cls(**filtered)


# ── WorldRule (v1.2 M1.1 新增) ───────────────────────────────────────────

@dataclass
class WorldRule:
    """世界规则实体 (修仙/科幻/奇幻通用)."""

    id: str = field(default_factory=lambda: gen_id("rule"))
    name: str = ""                                # 规则名 (必填)
    category: str = WorldRuleCategory.SYSTEM.value # 体系/地理/历史/宗教/科技/魔法/政治/其他
    description: str = ""                         # 详细定义 (10-200字)
    constraints: list[str] = field(default_factory=list)   # 硬约束列表 (违反 = 逻辑崩)
    examples: list[str] = field(default_factory=list)      # 3-5 个示例
    first_appearance: int | None = None           # 首次出现章节号
    related_entities: list[str] = field(default_factory=list)  # 关联角色/事件/伏笔 ID
    status: str = WorldRuleStatus.ESTABLISHED.value
    notes: str = ""
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)

    def __post_init__(self):
        # 必填校验
        if not self.name or not self.name.strip():
            raise ValueError("WorldRule.name 不能为空")
        # 校验 category
        valid_categories = {c.value for c in WorldRuleCategory}
        if self.category not in valid_categories:
            self.category = WorldRuleCategory.OTHER.value
        # 校验 status
        valid_statuses = {s.value for s in WorldRuleStatus}
        if self.status not in valid_statuses:
            self.status = WorldRuleStatus.DRAFT.value
        # id 不能空
        if not self.id:
            self.id = gen_id("rule")

    def touch(self) -> None:
        self.updated_at = _now_iso()

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "WorldRule":
        valid_keys = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in d.items() if k in valid_keys}
        # 兼容旧字段 (旧 world.json 是字符串)
        if "name" not in filtered or not filtered["name"]:
            # 旧字符串字段: 'description' 直接当 name
            if "description" in d and isinstance(d["description"], str):
                filtered["name"] = d["description"][:30]
                filtered["description"] = d.get("description", "")
            else:
                raise ValueError("WorldRule.name 不能为空")
        # 生成 id (如果没有)
        if "id" not in filtered or not filtered["id"]:
            filtered["id"] = gen_id("rule")
        # 默认值
        if "created_at" not in filtered:
            filtered["created_at"] = _now_iso()
        if "updated_at" not in filtered:
            filtered["updated_at"] = filtered["created_at"]
        return cls(**filtered)


# ── Entity 通用包装 ──────────────────────────────────────────────────────

@dataclass
class Entity:
    """通用实体包装, API 返回统一格式."""

    type: str          # EntityType.value
    id: str            # 主键 (Character 用 name, 其他用自增 id)
    data: dict         # 上述任一模型的 to_dict()

    def to_dict(self) -> dict:
        return {"type": self.type, "id": self.id, "data": self.data}

    @classmethod
    def from_dataclass(cls, obj: Any, entity_type: EntityType) -> "Entity":
        if isinstance(obj, Character):
            return cls(type=entity_type.value, id=obj.name, data=obj.to_dict())
        elif isinstance(obj, Event):
            return cls(type=entity_type.value, id=obj.event[:30], data=obj.to_dict())
        elif isinstance(obj, Foreshadow):
            return cls(type=entity_type.value, id=obj.foreshadow[:30], data=obj.to_dict())
        elif isinstance(obj, WorldRule):
            return cls(type=entity_type.value, id=obj.id, data=obj.to_dict())
        else:
            raise TypeError(f"Unsupported entity type: {type(obj).__name__}")