# 已知限制：C-TIRADS 与 ACR TI-RADS 在 canonical 层被合并成同一码

> 日期：2026-11-09｜本轮端到端验证的最后一块硬骨头

## 一、现象

真实端到端输出（正文完全正确）：
```
甲状腺 · TR4 — C-TIRADS 2021 计数法 —— ① 实性 +1 … = 1 分 → 4A 类（依据《C-TIRADS 2021》）
                ACR TI-RADS 2017 对照：实性(3)+低回声(2) = 5 分 → TR4
```

**前缀显示 `TR4`，正文主判读是 `4A 类`。** 医生看到两个不一致的码。

## 二、根因链（四层，逐层排查确认）

诊断日志给出决定性证据：
```
first_line_preview = <Answer>```
raw_from_first_line = None      ← 首行是 `<Answer>``` `，抽不到
raw_from_full_text  = 4a        ← 全文抽取**正确拿到 4a**
chosen              = 4a        ← 选择正确
canonical           = TR4       ← `_to_tr_canonical("4a")` 转成了 TR4
```

`_to_tr_canonical` 的实现：
```python
def _to_tr_canonical(raw: str | None) -> str | None:
    """分级码 → 专科 canonical TR1..TR5（**4a/4b/4c 收敛为主级 TR4**）"""
    major = grade_major_level("thyroid", raw)
    return f"TR{major}" if major is not None else None
```

**这是刻意的既有设计**（docstring 明写"下游报告 / 融合 / 评测一律按 TR1..TR5 消费"，
口径见 16 号 A.56）。

## 三、为什么这次变成了真问题

**以前是单体系**：系统只有 ACR TI-RADS，`4a → TR4` 是无损的。

**现在是双体系并列**：
| 体系 | 码 | 含义 | 恶性率 |
|---|---|---|---|
| C-TIRADS 2021 | `4A` | **低度**可疑恶性 | 2%~10% |
| ACR TI-RADS 2017 | `TR4` | **中度**可疑恶性 | （ACR 不分 4a/4b/4c） |

**两个主级都是"4"，但语义不同。** 合并后：
- 融合层看到 `TR4`，按 ACR 的"中度可疑"处理
- 而对 C-TIRADS 来说这是"低度可疑"
- **风险等级被系统性高估一档**

## 四、为什么本轮不改（改动面评估）

`TR1..TR5` 是**下游全局契约**：
- 融合层（`app/fusion/`）按 `TRx` 做冲突检测与分级上界
- 评测集与一致性校验用 `TRx`
- 数据库历史 797+ 条结论存的是 `TRx`
- `TI_RADS_LEVELS` 常量、`grade_major_level` 映射表

改成"C-TIRADS 码 + ACR 码双字段"意味着：
1. `AgentResponse` 加字段
2. 融合层、评测、报告、前端全部适配
3. 历史数据迁移

**这不是本轮能安全完成的范围**，且贸然改会破坏 8029 个测试中的大量契约。

## 五、本轮的缓解措施

**展示层已能自证**：正文里明确写了两套体系各自的计分与来源。
医生看到 `甲状腺 · TR4 —` 后紧跟的正文第一句就是：
> `C-TIRADS 2021 计数法 —— … = 1 分 → 4A 类（依据《C-TIRADS 2021》）`

**信息没有丢失，只是前缀的简写码取了主级。**

## 六、建议的根治方案（下一轮）

| 步 | 做什么 | 影响面 |
|---|---|---|
| 1 | `AgentResponse` 加 `grading_detail` 字段：`{system: "C-TIRADS 2021", code: "4A", major: 4}` | 小 |
| 2 | 展示层用 `grading_detail` 拼前缀（`甲状腺 · 4A 类（C-TIRADS）`） | 小 |
| 3 | 融合层仍用 `TRx` 主级（保持现有契约） | 零 |
| 4 | 评测集逐步补 `grading_detail` | 渐进 |

**关键**：第 1-2 步就能让医生看到正确的码，且不动下游契约。**这是低风险高收益的路径。**

## 七、我的判断

**这是一个真实缺陷，但不是本轮引入的**——它是"单体系设计"遇上"双体系需求"的必然结果。

**我不在报告里把它算作"已完成"**，而是明确列为已知限制，并给出可执行的根治路径。
