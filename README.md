# Hermes Weixin Multimodal Patch

这是一个针对 **Hermes Agent** 的微信多模态补丁仓库，主要修复图片 + 文字连续发送场景下的会话队列、`gateway_event` transcript 保真，以及 `/retry`、`/undo`、`/compress` 的回放一致性问题。

> 本仓库不是完整的 Hermes 镜像仓库，也不是官方分支；这里只公开与本次补丁直接相关的修改内容、说明文档和补丁文件。

---

## 这个补丁解决了什么问题

在微信（Weixin）真实使用场景里，用户很容易连续发送：

- 一张图片后紧跟一条说明文字
- 多张图片连续发送
- 图片发出后再补一句“看这个”“帮我分析这个”
- 发完图文后再使用 `/retry`
- 使用 `/undo` 或 `/compress` 后希望保留多模态上下文

Hermes 在旧行为下，容易出现这些问题：

1. 只有单个 pending 槽位，后来的消息覆盖前面的消息
2. 图片和说明文字之间的关系在忙会话中容易丢失
3. `/retry` 可能只能拿到纯文本，丢失媒体上下文
4. `/undo` 预览不能稳定显示用户原始输入
5. `/compress` 之后 surviving user turns 的 richer metadata 容易被洗掉
6. JSONL / SQLite 混合读取时，多模态 transcript 可能退化

---

## 这次补丁的核心改动

### 1. Pending 真源统一到 adapter queue

旧模型更接近“单个 pending 槽位”。  
本补丁改为按 session 维护：

- `Dict[str, deque[MessageEvent]]`

这样可以做到：

- 同一会话内 follow-up 保持顺序
- 图片 burst / 图片 + 快速说明可以合并成一个 logical turn
- 纯文本 follow-up 不再互相覆盖
- 重放时尽量保留真实到达顺序

---

### 2. 引入明确的 logical turn timing state

为了避免“图片后的快速说明”和“过了一会儿的新消息”混在一起，这次补丁把时间语义明确拆开：

- `timestamp`  
  原始入站事件时间，不作为可变 merge 时钟

- `logical_turn_started_at`  
  当前 logical turn 的首事件时间

- `last_merged_at`  
  最近一次并入该 logical turn 的事件时间

这允许 merge 规则同时依赖：

- 短 merge window
- 总 hard cap

从而避免把晚到文字/晚到图片误并入旧 turn。

---

### 3. media + text 合并规则变成客观时间规则

不再依赖模糊“意图判断”，而是使用明确时序规则：

- `photo + photo`
  - 仅在 merge window 与 hard cap 内合并
- `media-rooted turn + plain text follow-up`
  - 仅在 merge window 与 hard cap 内合并
- `text + text`
  - 不合并，始终保留为独立 queued turns
- 晚到文本 / 晚到图片
  - 超过窗口后不再并入旧 turn

这让行为更可预测，也更容易测试。

---

### 4. user transcript row 统一强制带 `gateway_event`

本补丁将新的 user transcript 契约统一为：

- 所有新的 user rows 都带 `gateway_event`
- 不只是多模态 turn
- 纯文本 turn 也必须有 richer schema

典型结构如下：

```json
{
  "role": "user",
  "content": "...当前送给模型的文本...",
  "timestamp": "...",
  "gateway_event": {
    "original_text": "...原始用户输入...",
    "message_type": "text|photo|voice|audio|video|document|...",
    "media_urls": [],
    "media_types": [],
    "structured_content": [
      {"type": "input_text", "text": "..."}
    ]
  }
}
