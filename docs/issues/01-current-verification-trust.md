# 修复审核等级派生：仅使用对当前内容有效的 verification 事件

关联：[#39](https://github.com/coolplayagent/okf-cli/issues/39)。状态：实现与验证完成，待合并发布。

## 问题与影响

修复前的 `Document::card` 仅根据 `verified.by` 派生审核等级，不比较 `verified.at` 与 `generated.at`。内容被修改后，历史人工审核仍可能将新内容标记为 `human-reviewed`，并使其通过审核等级筛选。缺少审核时间的事件也可能提升等级。

当前规则与诊断集中在 [CLI 契约](../cli-contract.md#派生与覆盖)，索引兼容说明见 [升级说明](../releases.md#未发布审核等级修复)。

## 证据与复现

- 审查源码：`bd9e5376de4505b3693fcb749e9683db9f6ef567`。
- 位置：`crates/okf-core/src/lib.rs` 的 `Document::card`；元数据诊断位于 `crates/okf-core/src/metadata.rs`。
- 2026-11-26 从上述未修改 HEAD 构建旧版，实测以下概念返回 `human-reviewed`；应用修复后的 Release 构建返回 `unverified`。完整范围见 [验证记录](../verification.md#最近验证)。

```yaml
type: Note
generated: {by: "agent/1", at: "2026-11-20T00:00:00Z"}
verified: {by: "human:alice", at: "2026-10-01T00:00:00Z"}
```

预期：该历史审核不能提升当前内容的等级，应为 `unverified`。

## 验收标准

- [x] 旧人工审核不会为更新后的内容提供 `human-reviewed` 等级。
- [x] 覆盖早于、等于、晚于更新时间，以及不存在 `generated` 的情况。
- [x] 覆盖缺失/非法审核时间、多事件、人工与非人工混合事件。
- [x] 实时、索引和候选筛选使用一致语义；旧版索引无需重建。
- [x] 在基于当前 HEAD 的构建上重现旧行为并验证修复，更新兼容说明。

参考：[OKF v0.3 §5.2–5.3](https://github.com/open-knowledge-format/open-knowledge-format/blob/main/SPEC.md#53-trust-tiers)。
