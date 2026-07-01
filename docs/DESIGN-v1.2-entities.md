# DESIGN v1.2 — 实体管理 (Entity Management)

> 状态: **M1.1 ✅ 完成** (commit 7df99ea) | M1.2 ⏳ 进行中
>
> 范围: 4 类核心实体的统一建模、CRUD、向 UI 暴露
>
> 设计依据: `docs/IMPLEMENTATION-v1.1-to-v2.0.md` v1.2 段 + 业务实体精细化方案

---

## 一、目标

把 novel_workflow 现有的 "散落 4 个 JSON 文件 + 字符串拼凑" 实体管理, 升级为 **类型化模型层 + 统一 CRUD API + 前端可视化**, 支撑:

1. **精细化生成**: 写作时把角色/事件/伏笔/**世界规则** 显式喂给 LLM, 提升一致性
2. **一致性扫描**: self_check.py 加 `world_rule_consistency` 检查 (LLM 判一致性)
3. **可视化**: Web UI 实体页 — 列表 / 编辑 / 关联图谱
4. **业务演化**: 仙侠/科幻/奇幻通用 — WorldRule 8 大类规则覆盖

---

## 二、4 类实体定义

### 2.1 Character (角色)

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `name` | str | ✅ | 主键 (唯一) |
| `role` | enum | | 主角/配角/反派/路人 |
| `traits` | str | | 性格特征 (短句) |
| `appearance` | str | | 外貌 (若有) |
| `importance` | enum | | 高/中/低 (决定 context 优先级) |
| `first_appearance` | int | | 首次登场章节号 |
| `relationship` | str | | 当前关系网快照 |
| `arc` | str | | 角色弧光 (起点 → 终点) |
| `aliases` | list[str] | | 别名/绰号 |
| `notes` | str | | 自由备注 |
| `created_at` / `updated_at` | iso | ✅ | 自动生成 |

### 2.2 Event (事件)

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `event` | str | ✅ | 事件简述 (20字内) |
| `significance` | str | | 对主线影响 |
| `consequences` | str | | 后续可能影响 |
| `chapter` | int | | 发生章节 |
| `participants` | list[str] | | 参与角色名 |
| `notes` | str | | |
| `extracted_at` | iso | ✅ | |

### 2.3 Foreshadow (伏笔)

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `foreshadow` | str | ✅ | 伏笔内容 (15字内) |
| `significance` | str | | 重要性 |
| `hints` | str | | 暗示位置/措辞 |
| `status` | enum | ✅ | 已埋/推进中/已回收/已放弃 |
| `planted_chapter` | int | | 埋设章节 |
| `resolved_chapter` | int | | 回收章节 |
| `resolved_at` | iso | | 回收时间 |
| `related_entities` | list[str] | | 关联 ID |
| `created_at` / `updated_at` | iso | ✅ | |

### 2.4 WorldRule (世界规则) — **NEW!**

修仙/科幻/奇幻/都市 通用, 8 大类规则覆盖。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | str | ✅ | `rule_xxxxxx` 短唯一 |
| `name` | str | ✅ | 规则名 (e.g. "灵根等级") |
| `category` | enum | ✅ | 体系/地理/历史/宗教/科技/魔法/政治/其他 |
| `description` | str | ✅ | 详细定义 (10-200字) |
| `constraints` | list[str] | ✅ | **硬约束列表** (违反 = 逻辑崩) |
| `examples` | list[str] | | 3-5 个示例 |
| `first_appearance` | int | | 首次出现章节 |
| `related_entities` | list[str] | | 关联角色/事件/伏笔 ID |
| `status` | enum | ✅ | 草案/已确立/已废弃 |
| `notes` | str | | |
| `created_at` / `updated_at` | iso | ✅ | |

**典型示例** (修仙):
```json
{
  "id": "rule_linggen_grade",
  "name": "灵根等级",
  "category": "体系",
  "description": "修士天赋分天/地/人三等, 每等三品, 共九品. 灵根品级决定修炼速度和功法适配.",
  "constraints": [
    "灵根品级先天决定, 不可后天改变",
    "高品修士对低品有灵压优势",
    "灵根等级不可跨级挑战成功"
  ],
  "examples": ["天灵根百年一遇", "人灵根最常见但难成大器"],
  "first_appearance": 3,
  "related_entities": ["char_xiao_yan", "rule_magic_grade"],
  "status": "已确立"
}
```

---

## 三、架构

```
                ┌─────────────────────────────────────┐
                │      Frontend (Vue 3 / 纯 HTML)    │
                │   实体列表页 / 编辑器 / 关联图谱   │
                └──────────────┬──────────────────────┘
                               │ REST/SSE
                ┌──────────────▼──────────────────────┐
                │   review_ui/app.py                  │
                │   GET /api/entities/<book>?type=…  │
                │   POST /api/entities/<book>         │
                │   PUT /api/entities/<book>/<id>     │
                │   DELETE /api/entities/<book>/<id>  │
                └──────────────┬──────────────────────┘
                               │
                ┌──────────────▼──────────────────────┐
                │   lib/memory.py: EntityStore        │ ← 统一 CRUD 层
                │   + lib/entity.py: 4 个 dataclass   │
                └──────────────┬──────────────────────┘
                               │ JSON 文件
                ┌──────────────▼──────────────────────┐
                │   projects/<book>/memory/           │
                │   ├── characters.json (dict[name])  │
                │   ├── events.json (list[dict])      │
                │   ├── foreshadowing.json (list)     │
                │   └── world.json (NEW: {rules, …})  │
                └─────────────────────────────────────┘
```

---

## 四、数据格式升级

### 4.1 向后兼容策略

旧 `world.json` 格式 `{key: text}` 仍是合法输入, 读取时**自动迁移**为:
```json
{
  "rules": {
    "rule_x7q2a9": {
      "name": "公司存在自动化邮件触发",
      "category": "其他",
      "description": "公司存在自动化邮件触发机制",
      "status": "已确立",
      ...
    }
  },
  "raw_notes": [...],
  "_legacy": {"公司存在自动化邮件触发": "...", ...}
}
```

`_legacy` 字段保留原始字符串, 防止误删。

### 4.2 其他 3 个文件保持不变

- `characters.json`: `{name: {fields}}` — 用 name 作为主键
- `events.json`: `list[dict]` — 用 event 文本前 30 字作为 ID
- `foreshadowing.json`: `list[dict]` — 用 foreshadow 文本前 30 字作为 ID

---

## 五、里程碑

### M1.1 ✅ (commit 7df99ea) — 实体基础
- ✅ `lib/entity.py` 4 个 dataclass
- ✅ `lib/memory.py` EntityStore + world.json 向后兼容
- ✅ 61 测试 (test_entity + test_memory_entity_store)
- ✅ 全套 196 tests pass

### M1.2 ⏳ — 抽取 + API
- `lib/extract.py`: EXTRACT_SYSTEM/EXTRACT_USER prompt 加 `new_world_rules` 字段
- `lib/extract.py` parse_extraction 加 world_rules 解析
- `review_ui/app.py`: 实体 CRUD 5 个 REST 端点
- `tests/test_review_ui_entities.py`: API 端到端测试
- 预计 +20 测试

### M1.3 ⏳ — 前端 + 迁移
- `review_ui/templates/entities.html`: 实体列表 + 编辑器 (纯 HTML)
- `review_ui/templates/world_rules.html`: WorldRule 专门页 (8 类筛选)
- `tools/migrate_world.py`: 一次性数据迁移脚本 (旧 world.json → 新结构)
- README/CHANGELOG 更新
- 预计 +10 测试

### M1.4 ⏳ — 一致性扫描
- `lib/self_check.py`: 加 `world_rule_consistency` 检查 (LLM 判章节是否违反硬约束)
- `review_ui/app.py`: 一致性报告端点
- 预计 +15 测试

### M1.5 ⏳ — Tag + Release
- 打 v1.2.0 tag, 合并路线图下一段 (评审增强 / 章节版本)

---

## 六、API 设计 (M1.2 详细)

```
GET    /api/entities/<book>?type=character|event|foreshadow|world_rule
       → 200 {entities: [{type, id, data}, ...], counts: {...}}

GET    /api/entities/<book>/<type>/<id>
       → 200 {entity} | 404

POST   /api/entities/<book>
       body: {type, data}
       → 201 {entity} | 400 (校验错)

PUT    /api/entities/<book>/<type>/<id>
       body: {fields: {...}}
       → 200 {entity} | 404

DELETE /api/entities/<book>/<type>/<id>
       → 204 | 404

GET    /api/entities/<book>/counts
       → 200 {character: N, event: N, ...}

POST   /api/entities/<book>/check_consistency
       → 200 {violations: [{rule_id, chapter, evidence, ...}]}
```

---

## 七、影响范围

### 7.1 LLM prompt 升级 (M1.2)

`EXTRACT_SYSTEM` 当前 4 实体输出, 升级为 5 字段:
```json
{
  "new_characters": [...],
  "updated_characters": [...],
  "new_events": [...],
  "new_foreshadowing": [...],
  "resolved_foreshadowing": [...],
  "world_updates": [...],          // 旧: 字符串数组
  "new_world_rules": [             // NEW: 结构化
    {"name": "灵根等级", "category": "体系", "description": "...", "constraints": [...]}
  ]
}
```

### 7.2 `merge_extraction` 兼容

`memory.merge_extraction` 已经处理:
- `world_updates` 字符串 → 自动包成 WorldRule(草稿态)
- `new_world_rules` dict → 直接 WorldRule.from_dict

### 7.3 UI 入口

`review_ui/templates/book.html` 加 "实体" 标签, 4 个子页:
- `/entities/<book>?type=character`
- `/entities/<book>?type=event`
- `/entities/<book>?type=foreshadow`
- `/entities/<book>?type=world_rule`

---

## 八、已知限制 / 待办

- [ ] **WorldRule ID 短**: 6 位 hex 有 16M 容量, 单本书够用, 跨书不保证唯一 (目前 EntityStore 不分 book)
- [ ] **关联图谱**: `related_entities` 字段已存, 但前端可视化 (D3.js force layout) 推到 v2.0
- [ ] **多语言**: 字段值都是中文, 英文小说需翻译层 (post-MVP)
- [ ] **删除关联**: delete_character 时未级联清理 foreshadow.related_entities 中的引用, 留作软删除 (toast 提示)

---

## 九、参考

- Keep a Changelog: https://keepachangelog.com/
- PEP 420 namespace package: https://peps.python.org/pep-0420/
- novel_workflow 路线图: `docs/IMPLEMENTATION-v1.1-to-v2.0.md`
- v1.1.2 lessons: `D:\self-improving\domains\novel-workflow.md` (L70-L75)