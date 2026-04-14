# Hermes Weixin Multimodal Patch

这是一个针对 **Hermes Agent** 的微信多模态补丁仓库，主要修复图片 + 文字连续发送场景下的会话队列、`gateway_event` transcript 保真，以及 `/retry`、`/undo`、`/compress` 的回放一致性问题。

> 本仓库不是完整的 Hermes 镜像仓库，也不是官方分支；这里只公开与本次补丁直接相关的修改内容、说明文档和补丁文件。

---

## 项目定位

本仓库的目标不是重新发布整个 Hermes，而是整理并公开这次针对微信多模态会话链路所做的 focused patch，方便：

- 记录这次修复的设计与实现思路
- 独立展示本轮补丁涉及的核心改动
- 后续整理成更适合 upstream 的 PR 或 patch set

---

## 这个补丁主要解决什么问题

在微信真实使用场景里，用户经常会连续发送：

- 一张图片后紧跟一条说明文字
- 多张图片连续发送
- 图片发出后再补一句说明
- 发完图文之后再执行 `/retry`
- 使用 `/undo` 或 `/compress` 后，希望保留原来的多模态上下文

旧行为下，容易出现这些问题：

1. 只有单个 pending 槽位，后来的消息覆盖前面的消息
2. 图片和说明文字之间的关系在 busy session 下容易丢失
3. `/retry` 可能只剩下纯文本，媒体上下文丢失
4. `/undo` 预览不能稳定显示原始用户输入
5. `/compress` 之后 surviving user turns 的 richer metadata 容易被洗掉
6. JSONL / SQLite 混合读取时，多模态 transcript 可能退化

---

## 本补丁的核心改动

### 1. Pending 真源统一到 adapter queue

旧模型更接近“单个 pending 槽位”。本补丁改为按 session 维护：

- `Dict[str, deque[MessageEvent]]`

这样可以做到：

- 同一会话内 follow-up 保持顺序
- 图片 burst / 图片 + 快速说明可以合并成一个 logical turn
- 纯文本 follow-up 不再互相覆盖
- replay 时尽量保留真实到达顺序

### 2. 引入明确的 logical turn timing state

为了避免“图片后的快速说明”和“过了一会儿的新消息”混在一起，本补丁把时间语义拆开为：

- `timestamp`
  - 原始入站事件时间
  - 不作为可变 merge 时钟
- `logical_turn_started_at`
  - 当前 logical turn 的首事件时间
- `last_merged_at`
  - 最近一次并入该 logical turn 的事件时间

### 3. user transcript row 统一强制带 `gateway_event`

所有新的 user transcript row 都带 `gateway_event`，包括纯文本 turn，不再只给多模态输入保 richer schema。

### 4. richer replay / rewrite 保真

本补丁修复并加固了以下路径：

- `/retry`
- `/undo`
- manual `/compress`
- auto-compress
- `load_transcript()`

重点是让 surviving user turns 尽量保留 richer metadata，而不是被重写成只剩 `role/content` 的简化结构。

### 5. 微信侧兼容修复

补丁同时包含与微信网关直接相关的兼容与日志修复：

- `send_image_file(image_path|path)` 参数兼容
- `sendmsg` ack / 空响应日志增强

---

## 本轮明确不做的事情

- 不做 SQLite schema migration
- 不把 SQLite 升级成 richer metadata 的唯一真源
- 不持久化完整原始平台 `raw_message`
- 不宣称已经覆盖 Hermes 全仓库所有集成测试

---

## 当前验证范围

本补丁主要通过 focused regression tests 与最小 smoke 验证确认行为，覆盖重点包括：

- 图片 + 两条快速说明 -> 1 logical turn
- delayed text 不 merge
- late photo 不 merge
- 纯文本 follow-up 保持两个 queued turns
- plain-text user turn 强制带 `gateway_event`
- voice -> `input_audio`
- video -> canonical `input_file`
- `/retry` 重建 multimodal `MessageEvent`
- `/undo` 预览优先 original text
- `/compress` 保留 surviving user rows 的 `gateway_event`
- auto-compress 同样保 richer user event
- equal-length 情况下 `load_transcript()` 优先 richer JSONL

---

## 已知边界

### 1. SQLite 仍然是 core-only

当前 richer replay 仍依赖 JSONL，而不是 SQLite metadata。

### 2. richer rehydrate 逻辑是保守恢复

对于重复内容的恢复，会优先避免误绑，而不是激进猜测。

### 3. 这是 focused patch，不是完整 Hermes 发布版

这里公开的是补丁思路和相关修改，不是完整 Hermes 仓库。

---

## 仓库内容说明

本仓库只用于公开与该补丁直接相关的内容，通常包括：

- 说明文档
- 变更说明
- 相关代码片段
- 最小测试 / 验证信息

不会包含：

- Hermes 全量代码镜像
- 服务器配置
- 本地绝对路径
- token / cookie / 密钥
- 与本补丁无关的私有交接内容

---

## 免责声明

本仓库仅公开补丁相关内容与实现思路，不代表 Hermes 官方发布版本。  
如需完整运行 Hermes，请使用 Hermes 官方仓库与官方发布流程。
