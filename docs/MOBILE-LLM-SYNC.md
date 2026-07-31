# Mobile App LLM 设置同步 (v1.3 M8)

**实现日期**: 2026-07-31
**状态**: ✅ 代码完成, 待 APK 重编译验证

## 背景

**之前**: mobile App `设置 → LLM 配置` 保存只写 `SharedPreferences` (本机)。`book.html` 改了 provider/model, mobile App 需要手动重开 + 测试连通性才能感知。Mobile 自己改了只影响本机, 其他设备/web 完全看不到。

**之后**: mobile App 保存时除了写本地, **同时** 调 `POST /api/config/<book>` 把 `llm_provider` + `llm_model` 写到每本书的 `config.json`, 覆盖所有 book。

## 数据流

```
mobile App 保存
    ↓
1. SharedPreferences.setString(provider/model/api_key)  ← 本地快速读
2. novelApi.listBooks()                                  ← 拿所有 book
3. for each book:
      novelApi.saveBookConfig(book, {llm_provider, llm_model})
                                                       ← POST /api/config/<book>
    ↓
4. SnackBar 显示 "已保存 + 同步到 N 本书" / "失败 X/Y"
```

后端 `POST /api/config/<book>` 已支持部分更新 (`llm_provider` / `llm_model` 字段), 无需后端改动.

## UI 变化

- "保存并测试" 按钮在同步期间显示 spinner + 文案改为 "保存并同步中…"
- 同步结果用 SnackBar 显示, 全成功 = 绿底, 部分失败 = 橙底 + 第一个错误信息

## 边界 / 限制

- **多设备并发**: 同一 provider/model 在两台设备改 = 后写赢 (config.json 直接覆盖)
- **删除的 book**: `listBooks()` 只返回存在的 book, 不会触发幽灵 API
- **离线**: `listBooks()` 失败 = 同步跳过, 本地 prefs 仍保存 (下次打开 web 会用 prefs 的默认值)
- **大量 book** (50+): 同步会触发 N 次 API. 单用户实际场景 1-3 本, 不是性能瓶颈

## 测试覆盖

- ✅ `flutter analyze lib/screens/llm_config.dart`: **No issues found** (2.5s)
- ⏸ Mobile E2E test: 待 `mobile/test/` 建测试基础设施后补 (M5 完成时未建 test, 是已知债)
- ⏸ APK 重编译: 待用户决策 (依赖 Android SDK + Gradle, ~10-15 min)

## 手动验证步骤 (重编 APK 后)

1. mobile App `设置 → LLM 配置` 选 provider `deepseek`, model `deepseek-chat`
2. 点 "保存并测试"
3. 期望 SnackBar: "已保存 + 同步到 N 本书" (绿底)
4. 浏览器打开 `https://your-vps/book/<book>`, 看 "AI 模型设置" 卡片显示 `deepseek · deepseek-chat` ✅
5. 在 book.html 改 model, mobile 重开 `LLM 配置` 页 → 应该看到新 default

## 相关 commit

- v1.3 M8 commit: `d390251` "feat(v1.3 M8): mobile LLM 配置保存时同步到所有 books 的 config.json"