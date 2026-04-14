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
这样做的目的：

减少 replay / rewrite 逻辑分支
保证 /retry、/undo、/compress 都能稳定依赖统一 schema
纯文本和多模态输入共享同一持久化契约
5. attachment classifier 统一
本补丁让 transcript 持久化和 inbound 预处理共享同一套附件分类逻辑，避免出现：

预处理阶段把它当音频
transcript 里却把它当文件
当前统一后的规则：

image -> input_image
voice / audio -> input_audio
video -> input_file
file / document -> input_file
text -> input_text
本轮中，video 暂时 canonical 成 input_file，优先保证 schema 稳定，不在这一轮引入 input_video。

6. /retry、/undo、/compress 的 richer 保真
/retry
优先从 gateway_event 重建：

original_text
message_type
media_urls
media_types
而不是只依赖 plain content。

/undo
预览优先显示：

gateway_event.original_text
/compress
保留 surviving original user rows 的 gateway_event，避免压缩后只剩下简化版 role/content。

auto-compress
同样恢复 surviving original user rows 的 richer metadata。

7. JSONL richer transcript 优先策略
本轮没有改 SQLite schema。
当前策略仍然是：

richer metadata 主要由 JSONL 保留
当 JSONL 与 SQLite 至少一样完整时，优先使用 JSONL
这保证了当前多模态回放链路先稳定工作。

本补丁明确不做的事情
以下内容不在本轮范围内：

不做 SQLite schema migration
不把 SQLite 直接升级成 richer metadata source of truth
不尝试持久化完整原始平台 raw_message
不在本轮引入新的 video transcript block 语义
不声称已经覆盖 Hermes 全仓库所有集成测试
当前验证范围
本补丁主要通过 focused regression tests 与最小 smoke 验证来确认行为。

覆盖的重点包括：

图片 + 两条快速说明 -> 1 logical turn
delayed text 不 merge
late photo 不 merge
纯文本 follow-up 保持两个 queued turns
plain-text user turn 强制带 gateway_event
voice -> input_audio
video -> canonical input_file
/retry 重建 multimodal MessageEvent
/undo 预览优先 original text
/compress 保留 surviving user rows 的 gateway_event
auto-compress 同样保 richer user event
equal-length 情况下 load_transcript() 优先 richer JSONL
当前已知边界
1. SQLite 仍然是 core-only
当前 richer replay 仍依赖 JSONL，而不是 SQLite metadata。

2. richer rehydrate 逻辑是保守恢复
对于重复内容的恢复，会优先避免误绑，而不是激进猜测。

3. 这是 focused patch，不是完整 Hermes 发布版
这里公开的是补丁思路和相关修改，不是完整 Hermes 仓库。

建议的后续演进方向
下一阶段更合理的方向是：

SQLite richer metadata migration
推荐未来给 SQLite 增加：

metadata_json
而不是只加：

gateway_event_json
原因是后续 transcript metadata 很可能继续增长，metadata_json 更有扩展性。

推荐路径：

SQLite 新增 metadata_json
新消息开始双写
JSONL 继续保留一段时间
逐步切换到 SQLite 为主
再做 lazy migration / opportunistic backfill
仓库内容说明
本仓库只用于公开与该补丁直接相关的内容，通常包括：

说明文档
变更说明
相关代码片段或 patch 文件
最小测试/验证信息
不会包含：

Hermes 全量代码镜像
服务器配置
本地绝对路径
token / cookie / 密钥
与本补丁无关的私有交接内容
推荐上游化方式
如果后续要把这套修改继续贡献回 Hermes 主仓库，更推荐走：

清理后的 focused commits
只提交改动文件
用 1~2 个 PR 分拆：
队列与 transcript 保真
Weixin 兼容修复（可选单独拆）
致谢
感谢 Hermes Agent 提供的基础架构与网关能力，这个补丁是在真实微信多模态会话场景下，对现有行为做的一次 focused hardening。

免责声明
本仓库仅公开补丁相关内容与实现思路，不代表 Hermes 官方发布版本。
如需完整运行 Hermes，请使用 Hermes 官方仓库与官方发布流程。

