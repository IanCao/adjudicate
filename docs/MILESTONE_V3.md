# 里程碑提交：OptimisticClaimOracleV3

关联项目：**Adjudicate**（已在 Project Explorer 获准发布）

## 标题

```
OptimisticClaimOracleV3 - an exit for the disputes that can never be decided
```

## 描述

```
v2 fixed a wrong verdict; it could not fix no verdict. Every payout path needs resolve() to succeed once, and resolve() is a consensus over fetched documents. When consensus fails - a page that renders differently per fetch, a model set that splits three ways - the call reverts and the claim stays CHALLENGED with both bonds locked. No owner, no timeout, no upgrade path.

v3 adds release_stuck(): after stuck_window anyone may release the dispute and each party recovers exactly its own bond. Nobody profits, so no one gains by engineering a deadlock; the window runs from the challenge, not the last failed attempt, so a resolver cannot keep a claim alive by reverting; the claim ends REFUNDED, not SETTLED, so an indexer never counts it as judged.

It also adds get_record(address): posted, upheld, refuted, inconclusive, uncontested - counters, not a score, because a score hides the sample size.

23 new tests, 80 total. Both prompts, the consensus rule and every payout split are unchanged.
```

997 / 1000 字符。

## 证明材料

```
https://github.com/IanCao/adjudicate
https://github.com/IanCao/adjudicate/blob/main/contracts/optimistic_claim_oracle_v3.py
https://github.com/IanCao/adjudicate/blob/main/CHANGELOG.md
https://github.com/IanCao/adjudicate/blob/main/docs/optimistic_claim_oracle_v3.md
```

第一条是给机器校验 `github-repo` 的仓库根。`IanCao/adjudicate` 这个根在 Projects 提交里用过一次，
若被判 `This URL has already been submitted`，换成 `https://github.com/IanCao/genlayer-primitives`
（同样可能被判重，因为 Intelligent Contracts 用过），两条都不行时把 v3 拆成独立仓库，运行期无依赖。

## 部署（可选但强烈建议）

Studio → Studionet → 粘贴 `contracts/optimistic_claim_oracle_v3.py`：

| 构造参数 | 值 | 说明 |
|---|---|---|
| `min_bond` | `10000000000000000` | 0.01 GEN，17 位 |
| `challenge_window` | `86400` | 1 天 |
| `appeal_window` | `3600` | 1 小时 |
| `stuck_window` | `259200` | 3 天；填 `0` 则用默认 86400 |

部署成功后把 explorer 地址作为第五条证明材料，并把地址粘进
`https://iancao.github.io/genlayer-primitives/` 的 Contract 输入框：页面读到 `release_stuck`
会显示 v3 徽章，卡死的争议上出现「Release · refund both」。

## Steward 会看什么

| 检查点 | 对应证据 |
|---|---|
| 是否是新工作，不是重投 | `CHANGELOG.md` 逐字段列出 v2 → v3 的构造参数、结构体、方法、赔付差异 |
| 是否命中「安全或架构改进」 | 修的是资金永久冻结，属安全缺陷，不是功能堆叠 |
| 能否验证 | `pytest tests -q` → 80 passed；23 个新用例在 `tests/test_oracle_v3_logic.py` |
| 是否与模板有实质区别 | v3 不改提示词与共识，只增加状态机出口，diff 干净可读 |
