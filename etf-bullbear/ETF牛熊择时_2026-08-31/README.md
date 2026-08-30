# ETF 牛熊择时 · 周度分析 (2026年8月31日)

> 自动化分析日期：2026-08-31 | 回测窗口：2014-01-01 ~ 2026-08-31（本地数据末日 2026-08-28）
> 数据源：腾讯前复权本地全历史缓存（5 ETF，D:/quant_data/etf_history）；宏观/宽度 88 标的缓存（quant-data-refresh）

## 📊 五档回测总览

| 等级 | 总收益% | 年化% | 最大回撤% | Sharpe | 胜率% | 交易 |
|------|---------|-------|-----------|--------|-------|------|
| base（无优化） | 144.8 | 8.2 | 65.4 | 0.46 | 43.1 | 58 |
| ① 防守超时 | 231.8 | 11.1 | 65.4 | 0.57 | 42.4 | 59 |
| ② +Regime确认 | 224.2 | 10.8 | 62.7 | 0.56 | 47.6 | 42 |
| ③ +分级防守 | 65.2 | 4.5 | 63.2 | 0.32 | 42.3 | 52 |
| ④ +移动止盈 | 49.8 | 3.6 | 64.0 | 0.29 | 46.0 | 63 |

> 参考买入持有：创业板50 +63.2% / 创业板ETF +163.6% / 沪深300 +207.6% / 50ETF +201.0% / 红利ETF +988.4%

## 🔍 当前市场状态

- **Regime：震荡市（sideways）**　*较上周（2026-08-17，牛市）发生翻转*
- GEM 收盘：**1.61** / MA200：**1.6643** / 斜率：**+1.1%** / 20日涨跌：**+1.9%** / 60日动量：**−19.5%** / 年化波动率：**41.71%** / 站上MA200：**否**
- 风格领先（近60日）：**红利 +3.9%**
- 宏观评分：**1（中性）**　PMI 50.3 / CPI 1.2 / PPI −0.9 / M2 8.6 / SHIBOR_1Y 1.4739
- 融合操作建议：**防守 / 轻仓，现金或低波动避风港为主**

## 🧪 分析当日实测参数（运行日快照，as_of=2026-08-28）

| 指标 | 取值 |
|------|------|
| GEM 收盘 / MA200 / 斜率 | 1.61 / 1.6643 / +1.1% |
| 是否站上 MA200 | 否（跌破 ~3.3%） |
| GEM 20日涨跌 / 60日动量 | +1.9% / −19.5% |
| GEM 年化波动率 | 41.71% |
| 宏观 PMI / CPI / PPI | 50.3 / 1.2 / −0.9 |
| 宏观 M2 / SHIBOR_1Y | 8.6 / 1.4739（Δ −0.0366） |
| 宏观评分 | 1（中性） |
| 融合 regime | 震荡市（sideways） |
| 建议动作 | 防守 / 轻仓，现金或低波动避风港为主 |

## ⚙️ 策略固定参数（关键阈值，来自 summary.json `params`）

| 参数组 | 参数 | 取值 |
|--------|------|------|
| 回测设置 | initial_cash / commission / lot | 1,000,000 / 0.0003 / 100 |
| 回测设置 | data_start / eval_start / eval_end | 2014-01-01 / 2014-01-01 / 2026-08-31 |
| 回测设置 | gem_proxy_start（代理切换日） | 2016-07-22 |
| v3 基础风控 | lock_days / consecutive_sig_days | 3 / 2 |
| v3 基础风控 | clean_days_needed / bull_sig_trigger | 10 / 2 |
| v3 基础风控 | drawdown_stop_pct | 15.0 |
| v3 基础风控 | momentum_low_window / momentum_threshold | 60 / 10.0 |
| v3 基础风控 | adaptive_window / min_periods / percentile | 500 / 126 / 80 |
| v3 基础风控 | vol20_floor / vol60_floor / amp_floor | 25.0 / 25.0 / 3.0 |
| v3 基础风控 | bull_vol20_50_cross | 28.0 |
| v5 配对轮动 | pair_ret_window / pair_z_window | 20 / 60 |
| v5 配对轮动 | pair_z_entry / pair_z_exit / pair_corr_min | 1.0 / 0.3 / 0.7 |
| v5 配对轮动 | pair_consecutive / slope_window / slope_threshold | 2 / 20 / 0.5 |
| v5 配对轮动 | side_dd_stop_pct | 15.0 |
| v6 宏观评分 | PMI 牛/熊 / CPI 牛/熊 | 51 / 49 / 2 / 3 |
| v6 宏观评分 | PPI 牛/熊 / M2 牛/熊 | 1 / −2 / 11 / 8 |
| v6 宏观评分 | shibor_window / shibor_threshold | 63 / 0.15 |
| v6 宏观评分 | macro_score 牛/熊阈值 | 2 / −2 |
| v7 风格选择 | style_mom_window | 20 |
| v10 优化开关 | MAX_DEFENSE_DAYS | 30 |
| v10 优化开关 | REGIME_CONFIRM_DAYS | 3 |
| v10 优化开关 | TIERED_RATIO | 0.5 |
| v10 优化开关 | TRAILING_STOP_PCT | 8.0 |
| v10 优化开关 | SLOPE_THRESHOLD | 0.5 |
| v10 优化开关 | MOMENTUM_THRESHOLD | 10.0 |
| v10 优化开关（各档） | opt1 防守超时 / opt2 Regime确认 / opt3 分级防守 / opt4 移动止盈 | base: 全关 / ①: T-F-F-F / ②: T-T-F-F / ③: T-T-T-F / ④: T-T-T-T |

## 📁 文件索引

- 仪表盘：`index.html`（双击打开）
- 回测数据：`gem50_bullbear_v10_*_equity.csv` / `*_regime.csv` / `*_trades.csv` / `*_regime_log.csv` / `*_style_log.csv`
- 五档指标：`gem50_bullbear_v10_*_summary.json`
- 月度研判：`monthly_review.json`
- 四段式报告：`report.md`
- GitHub：`https://github.com/lovweiyi/ipo-auto-report/tree/main/etf-bullbear/ETF牛熊择时_2026-08-31`

> ⚠️ 以上内容由 AI 基于公开信息整理生成，仅供参考，不构成任何投资建议或个股推荐。投资有风险，决策需谨慎。
