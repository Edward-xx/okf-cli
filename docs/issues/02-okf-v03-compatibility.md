# 补齐 OKF v0.3 格式、路径解析与替代概念导航兼容

建议优先级：P1；其中合法包被拒绝的问题优先处理。类型：兼容性。

## 问题与影响

项目规范基线仍为 v0.2。官方已升级至 v0.3，当前实现对合法新格式存在误拒绝、路径定位和关系发现缺口。CLI 包版本 `0.3.0` 不等于规范兼容版本。

| 领域 | 当前实现与缺口 |
| --- | --- |
| 根索引 | `validate_reserved` 仅允许根 `index.md` 的 `okf_version`，拒绝新增的 `title`、`description`、`license` 及扩展元数据 |
| 非概念 Markdown | 无 frontmatter 的 `README.md` 被报告为 `INVALID_OKF`，而新规范要求跳过此类非概念文件，可给警告但不影响格式合规 |
| 路径字段 | `resources.rs` 和 `neighbors.rs` 中使用 `resolve_link("index.md", resource)`，把嵌套文档的相对资源按包根解析 |
| 日期 | `timestamp_shape` 要求 `T/t`，未支持规范明确允许的纯日期及其 UTC 日起点含义 |
| Actor | `valid_actor` 只识别 `human:`、`process:` 或带 `/` 的形式，未接受 `team:` 等扩展 scheme |
| 图与生命周期 | `neighbors` 主要覆盖正文链接及来源字段，尚未完整处理其他标准路径字段与 `superseded_by` 导航 |

## 证据

源码基线：`bd9e5376de4505b3693fcb749e9683db9f6ef567`。

主要位置：`crates/okf-core/src/{lib,metadata}.rs`、`crates/okf-application/src/{resources,neighbors}.rs`。

2026-11-26 隔离实测：本地 Windows GNU `0.3.0` 二进制（来源 `f13dfbed04ceca403429fab41e087de6cdf9b6ef`，非当前 HEAD 重建）读取包含合法概念、普通 `README.md` 和下列根索引的包：

```yaml
---
okf_version: "0.3"
title: Audit
description: Audit bundle
---
```

`browse` 因 README 返回 `partial`、退出码 7；`validate` 返回两项失败、退出码 1，分别为 README 缺少 frontmatter 和根索引包含不允许字段。其余项目为静态代码发现，需增加运行回归。

## 建议实现

先建立 bundle 元数据与版本识别，再统一概念分类和路径字段解析。v0.3 相对路径从所属文件目录解析；旧版或无版本包按规范提供受控回退。未知版本采用可诊断的尽力读取，不应简单拒绝。

将替代关系纳入直接导航，保留原始来源，不自动将旧证据改写为新证据。审核事件时序已在本地实现并验证，见 [#39 记录](01-current-verification-trust.md)；纯日期和扩展 Actor scheme 仍由本项跟踪。

## 验收标准

- [ ] 增加 v0.2、v0.3、无版本与未知版本的兼容矩阵和固定语料。
- [ ] 根索引支持新元数据及扩展字段；子目录索引和日志规则单独验证。
- [ ] 非概念 Markdown 与非法概念准确区分；核对点路径排除规则。
- [ ] 日期、Actor 扩展与未知字段处理符合规范；非 `human:` 不派生人工审核。
- [ ] 正文链接及标准路径字段共用解析规则；测试嵌套相对路径、根路径、旧版回退与越界边界。
- [ ] 图边只将实际概念目标视为概念关系；覆盖标准路径字段。
- [ ] 提供 `superseded_by` 目标导航及断链、循环诊断，保留原概念引用。
- [ ] 全部新行为在当前构建验证，保持旧语料回归并更新文档。

参考：[官方 v0.3 变更清单](https://github.com/open-knowledge-format/open-knowledge-format/blob/main/SPEC.md#13-changes-from-v02)。实施时固定所审查的上游提交。
