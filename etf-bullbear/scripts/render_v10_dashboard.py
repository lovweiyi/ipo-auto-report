# -*- coding: utf-8 -*-
"""
Render a multi-tab comparison dashboard for the v10 four-step optimization backtest.

Tab 1 "对比"  : multi-line NAV comparison (line_chart) + KPI comparison (metric_table) + narrative text
Tab 2..6     : one tab per optimization level -> overview_chart (equity + drawdown + buy/sell markers) + trades_table

All Chinese (output_language = "zh"), rendered through the bundled dashboard_template.html.
"""
import os
import json
import sys
from pathlib import Path

REF = Path(os.environ.get("QB_REF", r"C:/Users/loveweiyi/.workbuddy/plugins/marketplaces/experts/plugins/strategy-backtest-expert/skills/quant-backtest-lab/reference"))
sys.path.insert(0, str(REF))
from render_dashboard import build_dashboard_data, render_dashboard  # noqa: E402

# ---- 输出目录解析: 环境变量 ETF_OUTPUT_DIR > --output-dir 参数 > 桌面 ----
_output_dir = os.environ.get("ETF_OUTPUT_DIR", "")
if not _output_dir:
    for i, a in enumerate(sys.argv[1:], 1):
        if a == "--output-dir" and i + 1 < len(sys.argv):
            _output_dir = sys.argv[i + 1]
            break
OUTPUT_DIR = Path(_output_dir) if _output_dir else Path(os.path.expanduser("~/Desktop"))
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# ---- 动态评估窗口末日: 从 base_summary.json 的 meta.end 读取 ----
import json as _json
_base_summary_path = OUTPUT_DIR / "gem50_bullbear_v10_base_summary.json"
_eval_end_disp = "最新"
if _base_summary_path.exists():
    try:
        _eval_end_disp = _json.loads(_base_summary_path.read_text(encoding="utf-8"))["meta"]["end"]
    except Exception:
        pass

LEVELS = [
    ("base", "基线(无优化)"),
    ("1",    "+① 防守超时"),
    ("2",    "+①+② Regime确认"),
    ("3",    "+①+②+③ 分级防守"),
    ("4",    "+①+②+③+④ 移动止盈"),
]

# ---------------------------------------------------------------------------
# 1. Per-level default modules (overview_chart + trades_table on a per-level tab)
# ---------------------------------------------------------------------------
per_level_modules = []   # collected modules for lvl_* tabs
level_summary = {}       # prefix -> summary dict
level_equity = {}        # prefix -> list[{date, value}]

for prefix, label in LEVELS:
    eq_csv = OUTPUT_DIR / f"gem50_bullbear_v10_{prefix}_equity.csv"
    tr_csv = OUTPUT_DIR / f"gem50_bullbear_v10_{prefix}_trades.csv"
    su_json = OUTPUT_DIR / f"gem50_bullbear_v10_{prefix}_summary.json"
    rd = build_dashboard_data(
        equity_csv=str(eq_csv),
        trades_csv=str(tr_csv),
        summary_json=str(su_json),
        language="zh",
        market="china_a",
    )
    level_summary[prefix] = rd["summary"]
    level_equity[prefix] = [{"date": p["date"], "value": float(p["value"])} for p in rd["equity_curve"]]

    tab_id = f"lvl_{prefix}"
    for mod in rd["modules"]:
        mtype = mod.get("type")
        if mtype == "overview_chart":
            m = dict(mod)
            m["tab"] = tab_id
            m["title"] = f"{label} · 净值与回撤"
            per_level_modules.append(m)
        elif mtype == "trades_table":
            m = dict(mod)
            m["tab"] = tab_id
            m["title"] = f"{label} · 交易明细"
            per_level_modules.append(m)

# ---------------------------------------------------------------------------
# 2b. 参数区块: 固定阈值 + 当日实测快照 (供对比页展示)
# ---------------------------------------------------------------------------
level_params = {}
level_snapshot = {}
for prefix, _ in LEVELS:
    su = OUTPUT_DIR / f"gem50_bullbear_v10_{prefix}_summary.json"
    try:
        _d = json.loads(su.read_text(encoding="utf-8"))
        level_params[prefix] = _d.get("params", {})
        level_snapshot[prefix] = _d.get("daily_snapshot") or {}
    except Exception:
        level_params[prefix] = {}
        level_snapshot[prefix] = {}

# 当日实测: 优先 monthly_review.json, 否则取 base 档 daily_snapshot
daily = {}
_mr = OUTPUT_DIR / "monthly_review.json"
if _mr.exists():
    try:
        daily = json.loads(_mr.read_text(encoding="utf-8"))
    except Exception:
        daily = level_snapshot.get("base", {}) or {}
else:
    daily = level_snapshot.get("base", {}) or {}

def _fv(v):
    if isinstance(v, bool):
        return "True" if v else "False"
    if isinstance(v, float):
        return f"{v:g}"
    return "—" if v is None else str(v)

# 2b-1. 各档优化开关对比 (metric_table)
_opt_switches = [
    ("① 防守超时 (MAX_DEFENSE_DAYS=30)", "opt_1_defense_timeout"),
    ("② Regime确认 (REGIME_CONFIRM_DAYS=3)", "opt_2_regime_confirm"),
    ("③ 分级防守 (TIERED_RATIO=0.5)", "opt_3_tiered_defense"),
    ("④ 移动止盈 (TRAILING_STOP_PCT=8)", "opt_4_trailing_stop"),
]
_opt_rows = []
for _name, _key in _opt_switches:
    _vals = []
    for _prefix, _ in LEVELS:
        _on = bool((level_params.get(_prefix, {}).get("v10_优化开关", {}) or {}).get(_key))
        _vals.append({"main": "✅ 开" if _on else "— 关", "raw": 1 if _on else 0})
    _opt_rows.append({"metric": _name, "values": _vals})

opt_table_module = {
    "type": "metric_table",
    "tab": "comparison",
    "title": "各档优化开关 (v10)",
    "subtitle": "✅=启用, —=未启用",
    "columns": ["优化项"] + [label for _, label in LEVELS],
    "rows": _opt_rows,
}

# 2b-2. 策略固定参数 (阈值) 分组表
_base_params = level_params.get("base", {})
_group_titles = [
    ("回测设置", "回测设置"),
    ("v3_基础风控", "v3 基础风控"),
    ("v5_配对轮动", "v5 配对轮动"),
    ("v6_宏观评分", "v6 宏观评分"),
    ("v7_风格选择", "v7 风格选择"),
    ("v10_优化开关", "v10 优化开关"),
]
_rows_html = []
for _k, _title in _group_titles:
    _grp = _base_params.get(_k, {})
    if not isinstance(_grp, dict) or not _grp:
        continue
    _rows_html.append(f'<tr><td colspan="2" style="background:rgba(127,127,127,.16);font-weight:600;padding:4px 8px;border:1px solid rgba(127,127,127,.35)">{_title}</td></tr>')
    for _pn, _pv in _grp.items():
        _rows_html.append(f'<tr><td style="border:1px solid rgba(127,127,127,.35);padding:4px 8px">{_pn}</td><td style="border:1px solid rgba(127,127,127,.35);padding:4px 8px">{_fv(_pv)}</td></tr>')
_params_html = (
    '<div style="color:inherit">'
    '<table style="border-collapse:collapse;width:100%;color:inherit;font-size:13px">'
    '<thead><tr style="background:rgba(127,127,127,.12)">'
    '<th style="border:1px solid rgba(127,127,127,.35);padding:4px 8px;text-align:left">参数 (固定阈值)</th>'
    '<th style="border:1px solid rgba(127,127,127,.35);padding:4px 8px;text-align:left">取值</th></tr></thead>'
    f'<tbody>{"".join(_rows_html)}</tbody></table></div>'
)

# 2b-3. 分析当日实测参数 (运行日快照)
_gem = daily.get("gem") or {}
_mdet = daily.get("macro_detail") or {}
def _g(key, src=None):
    _v = (src if src is not None else _gem).get(key)
    return "—" if _v is None else (f"{_v:g}" if isinstance(_v, float) else str(_v))
_snap_pairs = [
    ("分析日期 (as_of)", _g("as_of", daily)),
    ("融合 Regime", f'{daily.get("regime_cn","—")} ({daily.get("fused_regime","—")})'),
    ("建议动作", str(daily.get("recommended_action") or "—")),
    ("风格领先", str(daily.get("leader_style") or "—")),
    ("GEM 收盘", _g("close")),
    ("GEM MA200", _g("ma200")),
    ("GEM MA200 斜率 %", _g("slope_pct")),
    ("GEM 20日涨跌 %", _g("ret20_pct")),
    ("GEM 60日动量 %", _g("ret60_pct")),
    ("GEM 年化波动率 %", _g("ann_vol_pct")),
    ("GEM 站上 MA200", "是" if _gem.get("above_ma200") else "否"),
    ("宏观评分 (score)", _g("macro_score", daily)),
    ("PMI", _g("pmi", _mdet)),
    ("CPI", _g("cpi", _mdet)),
    ("PPI", _g("ppi", _mdet)),
    ("M2", _g("m2", _mdet)),
    ("SHIBOR_1Y", _g("shibor_1y", _mdet)),
]
_snap_rows = "".join(f'<tr><td style="border:1px solid rgba(127,127,127,.35);padding:4px 8px">{k}</td><td style="border:1px solid rgba(127,127,127,.35);padding:4px 8px">{v}</td></tr>' for k, v in _snap_pairs)
_snap_html = (
    '<div style="color:inherit">'
    '<table style="border-collapse:collapse;width:100%;color:inherit;font-size:13px">'
    '<thead><tr style="background:rgba(127,127,127,.12)">'
    '<th style="border:1px solid rgba(127,127,127,.35);padding:4px 8px;text-align:left">分析当日实测参数</th>'
    '<th style="border:1px solid rgba(127,127,127,.35);padding:4px 8px;text-align:left">取值</th></tr></thead>'
    f'<tbody>{_snap_rows}</tbody></table></div>'
)

param_modules = [
    opt_table_module,
    {"type": "custom_html", "tab": "comparison", "title": "策略固定参数 (阈值)",
     "width": "half", "html": _params_html},
    {"type": "custom_html", "tab": "comparison", "title": "分析当日实测参数 (运行日快照)",
     "width": "half", "html": _snap_html},
]

# ---------------------------------------------------------------------------
# 2. Comparison tab (line_chart + metric_table + text)
# ---------------------------------------------------------------------------
line_series = []
for prefix, label in LEVELS:
    line_series.append({"name": label, "points": level_equity[prefix]})

def fmt(v, digits=2, suffix="%"):
    if v is None:
        return "--"
    return f"{v:,.{digits}f}{suffix}"

cols = ["指标"] + [label for _, label in LEVELS]
def row(metric, key, digits=2, suffix="%", invert_dd=False, is_int=False):
    vals = []
    for prefix, _ in LEVELS:
        v = level_summary[prefix].get(key)
        if is_int:
            vals.append({"main": str(int(v)) if v is not None else "--"})
        else:
            vals.append({"main": fmt(v, digits, suffix),
                         "raw": (-abs(v) if invert_dd else v)})
    return {"metric": metric, "values": vals}

metric_rows = [
    row("总收益率", "total_return_pct", 2, "%"),
    row("年化收益率", "annual_return_pct", 2, "%"),
    row("最大回撤", "max_drawdown_pct", 2, "%", invert_dd=True),
    row("夏普比率", "sharpe", 3, ""),
    row("胜率", "win_rate_pct", 2, "%"),
    row("交易次数", "total_trades", is_int=True),
]

# 期末资产（万元）
final_vals = []
for prefix, _ in LEVELS:
    fv = level_summary[prefix].get("final_value") if "final_value" in level_summary[prefix] else None
    if fv is None:
        fv = level_summary[prefix].get("window_start_value", 0) * (1 + (level_summary[prefix].get("total_return_pct") or 0) / 100)
    final_vals.append({"main": f"{fv/10000:,.1f} 万", "raw": fv})
metric_rows.append({"metric": "期末资产", "values": final_vals})

# 动态结论文本（避免写死数字）
s = level_summary
base_ret = s["base"]["total_return_pct"]; l1_ret = s["1"]["total_return_pct"]
l2_ret = s["2"]["total_return_pct"]; l3_ret = s["3"]["total_return_pct"]; l4_ret = s["4"]["total_return_pct"]
base_sh = s["base"]["sharpe"]; l1_sh = s["1"]["sharpe"]
imp_pp = l1_ret - base_ret
conclusion_txt = (
    f"· 五个等级里，只有 +① 防守超时 真正改善了策略：总收益 {l1_ret:+.1f}%（基线 {base_ret:+.1f}%，"
    f"提升约 {imp_pp:.0f} 个百分点），夏普 {l1_sh:.2f}（基线 {base_sh:.2f}）。\n"
    f"· +② Regime 确认 基本中性（收益 {l2_ret:+.1f}%，略低于基线），属于“更稳但更钝”的取舍（胜率提升、交易更少）。\n"
    f"· +③ 分级防守（{l3_ret:+.1f}%）与 +④ 移动止盈（{l4_ret:+.1f}%）显著拖累策略："
    "分级防守在 2021/2022 熊市中仍保留一半创业板敞口，最大回撤放大到 ~62%；"
    "移动止盈把上涨趋势中的赢利过早截断，年化降到 ~4%。\n"
    "· 推荐保留 +① 单独使用，不要叠加 ③/④。注意全样本最大回撤高达 ~63%（2015-2016 创业板崩盘），"
    "策略在极端成长股崩盘中防御不足，这是其最核心的弱点。"
)

comparison_modules = [
    {
        "type": "line_chart",
        "tab": "comparison",
        "title": "净值曲线对比（初始 100 万）",
        "subtitle": f"2014-10 起（MA200预热后）~ {_eval_end_disp} · 各优化等级叠加对比",
        "series": line_series,
    },
    {
        "type": "metric_table",
        "tab": "comparison",
        "title": "关键指标对比",
        "subtitle": "同一数据 / 同一成本假设下的五档优化等级（含 2015-2016 创业板崩盘）",
        "columns": cols,
        "rows": metric_rows,
    },
    {
        "type": "text",
        "tab": "comparison",
        "title": "结论",
        "text": conclusion_txt,
    },
    {
        "type": "text",
        "tab": "comparison",
        "title": "关键假设与实现要点",
        "text": (
            f"· 数据区间：2014-01-01 起加载（MA200 预热），评估窗口 2014-10（首个有效信号）~ {_eval_end_disp}。\n"
            "· 数据源：本地全历史前复权日线缓存（D:/quant_data/etf_history，腾讯接口按年分块回补）；"
            "westock-data kline 自 2026-08 起单次仅返回约 1210 根日线，已不足以支撑 2014 起窗口，仅作降级备用。\n"
            "· 标的：创业板50ETF(159949.SZ)、沪深300ETF(510300.SH)、50ETF(510050.SH)、红利ETF(510880.SH)；"
            "2014~2016 创业板未上市前用创业板ETF(159915.SZ)作代理。\n"
            "· 信号在当日确认，次日开盘成交（防前视）；A 股 T+1、100 份整手、印花税+佣金已计入。\n"
            "· 风控：① 防守超时（防守状态超过 30 天触发 GEM 动量复核）；② 同侧 MA200 连续 3 日才切换；"
            "③ 分级防守（弱信号下 50% 创业板 + 50% 50ETF）；④ 移动止盈（自高点回撤 8% 离场）。\n"
            "· 期末强制平仓计入最后一笔交易。"
        ),
    },
    {
        "type": "text",
        "tab": "comparison",
        "title": "局限与偏差",
        "text": (
            "· 日线级别无法精确还原日内止损/止盈顺序，震荡市中买卖价与信号价可能有偏差。\n"
            "· 未建模冲击成本，大资金实盘滑点会更高。\n"
            "· 浦发/红利等 ETF 代理与真实标的存在幸存者与选择偏差。\n"
            "· 四步优化按 ①→②→③→④ 顺序叠加，参数未做样本外交叉验证，存在过拟合可能。"
        ),
    },
]

# ---------------------------------------------------------------------------
# 3. Assemble report_data
# ---------------------------------------------------------------------------
base_meta = json.loads((OUTPUT_DIR / "gem50_bullbear_v10_base_summary.json").read_text(encoding="utf-8"))["meta"]
report_data = {
    "meta": {
        "strategy_name": "风格轮动增强 v10 · 四步优化对比",
        "symbol": "159949.SZ / 510300.SH / 510050.SH / 510880.SH / 159915.SZ",
        "start": base_meta.get("start", "2015-01-01"),
        "end": base_meta.get("end", _eval_end_disp),
        "initial_cash": base_meta.get("initial_cash", 1000000.0),
        "window_start_value": base_meta.get("window_start_value", 1000000.0),
        "final_value": base_meta.get("final_value", 0.0),
        "market": "china_a",
        "generated_at": base_meta.get("generated_at"),
    },
    "summary": level_summary["base"],
    "equity_curve": level_equity["base"],
    "pnl_curve": [{"date": p["date"], "pnl": p["value"] - 1000000.0} for p in level_equity["base"]],
    "drawdown_curve": [],
    "trade_history": [],
    "ui": {
        "subtitle": "多档优化等级对比仪表盘",
        "active_tab": "comparison",
        "tabs": [
            {"id": "comparison", "label": "对比"},
            {"id": "lvl_base", "label": "基线"},
            {"id": "lvl_1", "label": "+① 防守超时"},
            {"id": "lvl_2", "label": "+①②"},
            {"id": "lvl_3", "label": "+①②③"},
            {"id": "lvl_4", "label": "+①②③④"},
        ],
        "language": "zh",
    },
    "modules": comparison_modules + param_modules + per_level_modules,
}

out_path = OUTPUT_DIR / "index.html"
render_dashboard(report_data, output_path=str(out_path))
print(f"[OK] dashboard written -> {out_path}")

# Quick self-check of embedded data sizes
print(f"[check] comparison line series points: {[len(s['points']) for s in line_series]}")
print(f"[check] per-level modules count: {len(per_level_modules)}")
