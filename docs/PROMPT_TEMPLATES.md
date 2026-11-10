# 纯文本 Prompt 模板索引

## 为什么需要它

很多 AI（豆包网页版、Claude Web、部分手机端助手）**无法执行 Python**。如果 Skill 只提供代码，这些平台就完全用不了。

本项目的做法是：每个 `SKILL.md` 的第 6 节都附一套**自包含的纯文本 Prompt 模板** —— 把规则、公式、分段表、演算示例、输出格式全部写进文本里。人类把这段 MD 发给任意大模型，模型照着查表、手工计算，就能产出与 Python 一致的结果。

## 索引

| Skill | 模板位置 | 让 AI 做什么 | 是否需要其它文件 |
| --- | --- | --- | --- |
| 1 数据管道 | `skill1_data_pipeline/SKILL.md` §6 | 清洗成标准 10 列、判断交易日 / T-1、标的池过滤 | 否 |
| 2 打分引擎 | `skill2_scoring_engine/SKILL.md` §6 | 硬过滤 + 五维分段查表 + 加权求和 → Top3 | 否 |
| 3 模拟盘 | `skill3_paper_trading/SKILL.md` §6 | 建仓手数计算、平仓优先级判定、盈亏与复盘 | 否 |
| 4 回测 | `skill4_backtest/SKILL.md` §6 | 逐日推演净值、回撤、胜率 | 否 |

每套模板都是**自包含**的：不需要 AI 读 `weights.json`，不需要联网，不需要其它 Skill 的文档。

## 通用投递格式

把 `SKILL.md` 里 `【开始】` 到 `【结束】` 之间的代码块内容复制出来，按下面格式发送：

```text
<粘贴模板全文>

【我的数据】
date,code,name,price,cb_change_pct,stock_change_pct,cb_stock_gap,volume_ratio,premium_rate,amount
2026-11-10,113050,南银转债,128.60,1.20,4.80,3.60,2.10,8.50,385000000
2026-11-10,123138,丝路转债,132.45,0.85,5.20,4.35,1.95,12.30,156000000
...
```

## 跨 AI 兼容性对照

| 平台 | 能跑 Python | 推荐用法 | 注意 |
| --- | --- | --- | --- |
| WorkBuddy / CodeBuddy | ✅ | 直接运行 `scripts/*.py`；可选 `westock` 取数 | 用 `python3` |
| 本地终端 / CI | ✅ | `bash scripts/run_all.sh` | 建议 venv |
| Claude (API/Desktop) | ✅ / ❌ | 能跑就跑 Python；不能跑就用模板 | 长表格建议分批给 |
| 豆包 / 网页版助手 | ❌ | **只用纯文本模板** | 一次别贴超过 ~50 行数据 |
| 手机端 AI | ❌ | 只用模板，且优先用 Skill 2 的精简示例 | 输出格式可能被压缩 |

## 保证 Python 与 AI 手算一致的关键

1. **用 `absolute_bands` 而非 `minmax`**：逐行独立打分，AI 拿到 1 行也能算；`minmax` 必须知道当日全样本的最大/最小值。
2. **分段表写成闭开区间** `min <= x < max`，边界不歧义。
3. **单位统一为百分点**，避免 4.8 / 0.048 混淆。
4. **模板内附一条完整演算**（逐项乘法展开），模型可以对照校验自己没算错。
5. **同分排序规则**写死（按 code 升序），避免不同模型给出不同 Top3。

## 维护约定 ⚠️

改了 `weights.json` 之后，**必须同步**更新以下 3 处，否则 Python 与 AI 手算会给出不同结果：

1. `skill2_scoring_engine/SKILL.md` §6 的分段表与权重
2. `skill4_backtest/SKILL.md` §6 的分段表
3. `skill3_paper_trading/SKILL.md` §6 中引用的分数示例（如有）

建议改动后跑一次 `bash scripts/run_all.sh`，把 Skill 2 的真实输出与模板里的演算示例对拍。
