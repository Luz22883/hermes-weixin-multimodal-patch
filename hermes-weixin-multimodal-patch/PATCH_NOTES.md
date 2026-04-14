# PATCH_NOTES

## 补丁目标

本补丁聚焦于 Hermes Agent 在微信（Weixin）场景下的多模态会话 hardening，目标是让图片、文字 follow-up 与 transcript replay 在 busy session 下更稳定、更可重放。

## 核心改动

### 1. Pending 真源统一

- 单 pending 槽位升级为 per-session queue
- 队列类型统一为 `Dict[str, deque[MessageEvent]]`
- follow-up 不再依赖单个可变文本 shadow state

### 2. 明确 logical turn timing state

新增并固定以下语义：

- `timestamp`：原始入站事件时间
- `logical_turn_started_at`：当前 logical turn 首事件时间
- `last_merged_at`：最近一次并入该 turn 的事件时间

### 3. 时间窗口驱动的 merge 规则

- `photo + photo`：仅在 merge window 与 hard cap 内合并
- `media-rooted turn + plain text follow-up`：仅在 merge window 与 hard cap 内合并
- `text + text`：不合并
- 晚到文本 / 晚到图片：超过窗口后不再并入旧 turn

### 4. transcript richer 契约统一

所有新的 user transcript row 均带 `gateway_event`，包括纯文本 turn。

典型字段包括：

- `original_text`
- `message_type`
- `media_urls`
- `media_types`
- `structured_content`

### 5. replay / rewrite 保真修复

修复并加固了以下路径对 richer user event 的保真：

- `/retry`
- `/undo`
- manual `/compress`
- auto-compress
- `rewrite_transcript()`
- `load_transcript()`

### 6. 微信兼容修复

- `send_image_file(image_path|path)` 参数兼容
- `sendmsg` acknowledgement / 空响应日志增强

## 非目标

本轮不包含：

- SQLite schema migration
- richer metadata 写入 SQLite
- 完整 upstream CI / 全仓集成验证

## 重点验证

已补充 focused regression tests，覆盖：

- 图 + 快速文本合并
- delayed text / late photo 不误并
- plain-text `gateway_event`
- `/retry` richer rebuild
- `/undo` preview 保真
- compress / auto-compress 对 surviving user rows 的 `gateway_event` 保留

## 已知边界

- richer replay 仍依赖 JSONL
- SQLite 仍为 core-only
- rehydrate 对重复内容采用保守恢复策略，优先避免误绑
