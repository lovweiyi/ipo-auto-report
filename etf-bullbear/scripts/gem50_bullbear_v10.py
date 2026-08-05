"""
风格轮动增强 v10: 2014起+四步优化 宏观融合 + 多ETF风格选择
==========================================
在 v6 基础上适度扩展ETF宇宙, 每个regime都体现风格选择:

ETF宇宙 (5只, 覆盖4种风格):
  159949.SZ  创业板50ETF    — 成长风格 (小盘高弹性)
  510300.SH  沪深300ETF     — 大盘均衡 (NEW)
  510050.SH  上证50ETF      — 大盘价值 (超级大盘蓝筹)
  510880.SH  红利ETF        — 防御价值 (NEW)
  159915.SZ  创业板ETF      — 配对参考 (震荡市z-score)

风格选择逻辑:
  牛市: 成长(159949) vs 大盘均衡(510300) 20日动量择强 (10%超额阈值)
        波动率防守时切上证50(510050)
  熊市: 纯现金 (红利ETF测试发现不防御, 已移除)
  震荡: 159949 ↔ 159915 配对轮动 (继承v5/v6)

宏观评分体系 (5指标) [继承v6]:
  PMI/CPI/PPI/M2/SHIBOR, 得分>=2=宏观牛, <=-2=宏观熊
  融合规则: 宏观只做风控降级 (bull->sideways, sideways->bear)

防前视: 宏观数据按发布日期(INFO_DATE)对齐
T+1执行: 信号日收盘判断, 次日开盘成交
"""

import subprocess, json, re, csv, math, os, sys
from datetime import datetime, date
from pathlib import Path

import pandas as pd
import numpy as np

NODE_EXE = os.environ.get("NODE_EXE", "C:/Users/loveweiyi/.workbuddy/binaries/node/versions/22.22.2/node.exe")
WESTOCK_JS = os.environ.get("WESTOCK_JS", "C:/Users/loveweiyi/.workbuddy/plugins/marketplaces/experts/plugins/strategy-backtest-expert/skills/westock-data/scripts/index.js")

INITIAL_CASH = 1_000_000
COMMISSION = 0.0003
LOT_SIZE = 100
DATA_LIMIT = 5000
# 本地全历史日线缓存 (由 build_etf_history.py 维护, 腾讯前复权, 可回溯至上市日)
# CI 覆盖: ETF_HISTORY_DIR 环境变量指向仓库内 data/etf_history
ETF_HISTORY_DIR = os.environ.get("ETF_HISTORY_DIR", "D:/quant_data/etf_history")

DATA_START = "2014-01-01"
EVAL_START = "2014-01-01"
# 评估窗口末日: 默认=运行当日(YYYY-MM-DD); 可用环境变量 EVAL_END 或 --eval-end 覆盖
EVAL_END = os.environ.get("EVAL_END", "")
if not EVAL_END:
    for _i, _a in enumerate(sys.argv[1:], 1):
        if _a == "--eval-end" and _i + 1 < len(sys.argv):
            EVAL_END = sys.argv[_i + 1]
            break
if not EVAL_END:
    EVAL_END = datetime.now().strftime("%Y-%m-%d")

# ==== v3 继承参数 ====
LOCK_DAYS = 3
CONSECUTIVE_SIG_DAYS = 2
CLEAN_DAYS_NEEDED = 10
BULL_SIG_TRIGGER = 2
DRAWDOWN_STOP_PCT = 15.0
MOMENTUM_LOW_WINDOW = 60
MOMENTUM_THRESHOLD = 10.0
ADAPTIVE_WINDOW = 500
ADAPTIVE_MIN_PERIODS = 126
ADAPTIVE_PERCENTILE = 80
AMP_FLOOR = 3.0
VOL20_FLOOR = 25.0
VOL60_FLOOR = 25.0
BULL_VOL20_50_CROSS = 28.0

# ==== v5 新增: 配对轮动参数 ====
PAIR_RET_WINDOW = 20        # 涨幅差值回看窗口 (20日)
PAIR_Z_WINDOW = 60          # z-score 滚动窗口 (60日)
PAIR_Z_ENTRY = 1.0          # z-score 入场阈值
PAIR_Z_EXIT = 0.3           # z-score 出场阈值 (回归中性)
PAIR_CORR_MIN = 0.70        # 最低相关性才交易
PAIR_CONSECUTIVE = 2        # 连续2日确认

# ==== v5 新增: 三态切换控制 ====
SLOPE_WINDOW = 20            # MA200斜率回看天数
SLOPE_THRESHOLD = 0.5        # MA200斜率阈值(%): |slope|<此值=震荡
SIDE_DD_STOP_PCT = 15.0     # 震荡市回撤止损线

# ==== v6 新增: 宏观评分参数 ====
MACRO_CSV = os.environ.get("MACRO_CSV", os.path.join(os.path.dirname(os.path.abspath(__file__)), "macro_data.csv"))
# PMI 阈值
MACRO_PMI_BULL = 51.0
MACRO_PMI_BEAR = 49.0
# CPI 阈值
MACRO_CPI_BULL = 2.0    # CPI<2% → 低通胀, 宽松空间
MACRO_CPI_BEAR = 3.0    # CPI>3% → 通胀压力
# PPI 阈值
MACRO_PPI_BULL = 1.0    # PPI>1% → 需求复苏
MACRO_PPI_BEAR = -2.0   # PPI<-2% → 通缩
# M2 阈值
MACRO_M2_BULL = 11.0    # M2>11% → 货币扩张
MACRO_M2_BEAR = 8.0     # M2<8% → 货币收缩
# SHIBOR 趋势
MACRO_SHIBOR_WINDOW = 63    # ~3个月交易日
MACRO_SHIBOR_THRESHOLD = 0.15  # 变化>0.15%才算趋势
# 宏观得分阈值
MACRO_SCORE_BULL = 2     # ≥2 = 宏观牛市
MACRO_SCORE_BEAR = -2    # ≤-2 = 宏观熊市

# ==== v7 新增: 风格选择参数 ====
# 牛市: GEM(159949)为默认持仓, 动量不足时fallback到HS300(510300)再到50ETF(510050)
# 无主动风格轮动 — HS300仅作为GEM动量失败时的中间选项
STYLE_MOM_WINDOW = 20         # 20日动量窗口 (用于图表展示)
# 熊市: 纯现金 (红利ETF测试发现不防御, 已移除)


# ==== v10 新增: 四步优化开关 (由命令行 LEVEL 控制) ====
# LEVEL 取值: base / 1 / 2 / 3 / 4
#   base = 仅2014扩展, 禁用所有优化
#   1    = +①防守超时
#   2    = +①+②regime确认
#   3    = +①+②+③分级防守
#   4    = +①+②+③+④移动止盈
# ---- 输出目录解析: 环境变量 ETF_OUTPUT_DIR > --output-dir 参数 > 桌面 ----
_output_dir = os.environ.get("ETF_OUTPUT_DIR", "")
if not _output_dir:
    for i, a in enumerate(sys.argv[1:], 1):
        if a == "--output-dir" and i + 1 < len(sys.argv):
            _output_dir = sys.argv[i + 1]
            break
OUTPUT_DIR = Path(_output_dir) if _output_dir else Path(os.path.expanduser("~/Desktop"))
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# ---- LEVEL 解析: 第一个非 -- 开头且非 --output-dir 值的参数 ----
_level_args = []
_skip_next = False
for a in sys.argv[1:]:
    if _skip_next:
        _skip_next = False
        continue
    if a == "--output-dir":
        _skip_next = True
        continue
    if not a.startswith("--"):
        _level_args.append(a)
LEVEL = _level_args[0] if _level_args else "base"

OPT_1_DEFENSE_TIMEOUT = LEVEL in ("1", "2", "3", "4")   # ① 防守超时
OPT_2_REGIME_CONFIRM = LEVEL in ("2", "3", "4")         # ② Regime确认
OPT_3_TIERED_DEFENSE = LEVEL in ("3", "4")              # ③ 分级防守
OPT_4_TRAILING_STOP = LEVEL in ("4",)                   # ④ 移动止盈
# 优化参数
MAX_DEFENSE_DAYS = 30             # ① 最大防守天数
REGIME_CONFIRM_DAYS = 3           # ② Regime确认天数
TIERED_RATIO = 0.5                # ③ 分级防守比例 (50%GEM)
TRAILING_STOP_PCT = 8.0           # ④ 移动止损% (从峰值回撤)
# v10 代理逻辑: 159949 2016-07才上市, 之前用159915做GEM proxy
GEM_159949_START = "2016-07-22"   # 159949.SZ 上市日期

def get_opt_desc():
    opts = []
    if OPT_1_DEFENSE_TIMEOUT: opts.append("①防守超时")
    if OPT_2_REGIME_CONFIRM: opts.append("②Regime确认")
    if OPT_3_TIERED_DEFENSE: opts.append("③分级防守")
    if OPT_4_TRAILING_STOP: opts.append("④移动止盈")
    return "+".join(opts) if opts else "基线(仅2014扩展)"

PREFIX = f"gem50_bullbear_v10_{LEVEL}"


def collect_params():
    """汇总本策略全部关键参数(固定阈值), 便于结果可复现与跨档对比."""
    return {
        "回测设置": {
            "initial_cash": INITIAL_CASH,
            "commission": COMMISSION,
            "lot_size": LOT_SIZE,
            "data_limit": DATA_LIMIT,
            "data_start": DATA_START,
            "eval_start": EVAL_START,
            "eval_end": EVAL_END,
            "gem_proxy_start": GEM_159949_START,
        },
        "v3_基础风控": {
            "lock_days": LOCK_DAYS,
            "consecutive_sig_days": CONSECUTIVE_SIG_DAYS,
            "clean_days_needed": CLEAN_DAYS_NEEDED,
            "bull_sig_trigger": BULL_SIG_TRIGGER,
            "drawdown_stop_pct": DRAWDOWN_STOP_PCT,
            "momentum_low_window": MOMENTUM_LOW_WINDOW,
            "momentum_threshold": MOMENTUM_THRESHOLD,
            "adaptive_window": ADAPTIVE_WINDOW,
            "adaptive_min_periods": ADAPTIVE_MIN_PERIODS,
            "adaptive_percentile": ADAPTIVE_PERCENTILE,
            "amp_floor": AMP_FLOOR,
            "vol20_floor": VOL20_FLOOR,
            "vol60_floor": VOL60_FLOOR,
            "bull_vol20_50_cross": BULL_VOL20_50_CROSS,
        },
        "v5_配对轮动": {
            "pair_ret_window": PAIR_RET_WINDOW,
            "pair_z_window": PAIR_Z_WINDOW,
            "pair_z_entry": PAIR_Z_ENTRY,
            "pair_z_exit": PAIR_Z_EXIT,
            "pair_corr_min": PAIR_CORR_MIN,
            "pair_consecutive": PAIR_CONSECUTIVE,
            "slope_window": SLOPE_WINDOW,
            "slope_threshold": SLOPE_THRESHOLD,
            "side_dd_stop_pct": SIDE_DD_STOP_PCT,
        },
        "v6_宏观评分": {
            "macro_pmi_bull": MACRO_PMI_BULL,
            "macro_pmi_bear": MACRO_PMI_BEAR,
            "macro_cpi_bull": MACRO_CPI_BULL,
            "macro_cpi_bear": MACRO_CPI_BEAR,
            "macro_ppi_bull": MACRO_PPI_BULL,
            "macro_ppi_bear": MACRO_PPI_BEAR,
            "macro_m2_bull": MACRO_M2_BULL,
            "macro_m2_bear": MACRO_M2_BEAR,
            "macro_shibor_window": MACRO_SHIBOR_WINDOW,
            "macro_shibor_threshold": MACRO_SHIBOR_THRESHOLD,
            "macro_score_bull": MACRO_SCORE_BULL,
            "macro_score_bear": MACRO_SCORE_BEAR,
        },
        "v7_风格选择": {
            "style_mom_window": STYLE_MOM_WINDOW,
        },
        "v10_优化开关": {
            "level": LEVEL,
            "opt_1_defense_timeout": OPT_1_DEFENSE_TIMEOUT,
            "opt_2_regime_confirm": OPT_2_REGIME_CONFIRM,
            "opt_3_tiered_defense": OPT_3_TIERED_DEFENSE,
            "opt_4_trailing_stop": OPT_4_TRAILING_STOP,
            "max_defense_days": MAX_DEFENSE_DAYS,
            "regime_confirm_days": REGIME_CONFIRM_DAYS,
            "tiered_ratio": TIERED_RATIO,
            "trailing_stop_pct": TRAILING_STOP_PCT,
        },
        "etf_pool": [
            "159949.SZ 创业板50ETF(成长主线)",
            "510300.SH 沪深300ETF(大盘均衡)",
            "510050.SH 上证50ETF(大盘价值/防守)",
            "510880.SH 红利ETF(防御价值)",
            "159915.SZ 创业板ETF(震荡配对/早期代理)",
        ],
    }


def load_daily_snapshot(output_dir):
    """从同目录 monthly_review.json 提取分析当日实测参数(运行日快照). 缺失则返回 None."""
    try:
        p = Path(output_dir) / "monthly_review.json"
        if not p.exists():
            return None
        d = json.loads(p.read_text(encoding="utf-8"))
        gem = (d.get("etfs") or {}).get("159949.SZ") or {}
        macro = d.get("macro") or {}
        detail = macro.get("detail") or {}
        return {
            "as_of": d.get("as_of"),
            "generated_at": d.get("generated_at"),
            "fused_regime": d.get("fused_regime"),
            "regime_cn": d.get("regime_cn"),
            "recommended_action": d.get("recommended_action"),
            "leader_style": d.get("leader_style"),
            "gem": {
                "date": gem.get("date"),
                "close": gem.get("close"),
                "ma200": gem.get("ma200"),
                "slope_pct": gem.get("slope"),
                "ret20_pct": gem.get("ret20"),
                "ret60_pct": gem.get("ret60"),
                "ann_vol_pct": gem.get("ann_vol"),
                "above_ma200": gem.get("above_ma200"),
            },
            "macro_score": macro.get("score"),
            "macro_regime": macro.get("regime"),
            "macro_detail": {
                "pmi": detail.get("pmi"),
                "cpi": detail.get("cpi"),
                "ppi": detail.get("ppi"),
                "m2": detail.get("m2"),
                "shibor_1y": detail.get("shibor_1y"),
                "shibor_delta": detail.get("shibor_delta"),
            },
        }
    except Exception:
        return None



# ═══════════════════════════════════════════════════
# 数据加载
# ═══════════════════════════════════════════════════

def westock_kline(code, limit=5000):
    cmd = [NODE_EXE, WESTOCK_JS, "kline", code, "--period", "day",
           "--limit", str(limit), "--fq", "qfq"]
    last_err = None
    for attempt in range(3):
        try:
            result = subprocess.run(cmd, capture_output=True, text=True)
            output = result.stdout.strip()
            if not output:
                raise RuntimeError(f"empty output (attempt {attempt+1})")
            lines = output.split('\n')
            sep_found = False
            data_lines = []
            for line in lines:
                line = line.strip()
                if not line: continue
                if line.startswith('|') and '---' in line:
                    sep_found = True; continue
                if sep_found and line.startswith('|') and '日期' not in line:
                    data_lines.append(line)
            if not data_lines:
                raise RuntimeError(f"no data rows (attempt {attempt+1})")
            rows = []
            for dl in data_lines:
                cells = [c.strip() for c in dl.split('|')[1:-1]]
                if len(cells) < 6: continue
                rows.append({
                    'date': cells[0], 'open': float(cells[1].replace(',', '')),
                    'close': float(cells[2].replace(',', '')),
                    'high': float(cells[3].replace(',', '')),
                    'low': float(cells[4].replace(',', '')),
                })
            df = pd.DataFrame(rows)
            df['date'] = pd.to_datetime(df['date'])
            df = df.sort_values('date').reset_index(drop=True)
            return df
        except Exception as e:
            last_err = e
            print(f"  [retry {attempt+1}/3] westock kline {code}: {e}")
            import time as _t; _t.sleep(2)
    raise RuntimeError(f"westock-data kline {code} failed after 3 attempts: {last_err}")


def load_local_history(code):
    """读取 build_etf_history.py 落盘的全历史日线 (date,open,close,high,low)."""
    path = os.path.join(ETF_HISTORY_DIR, f"{code}.csv")
    if not os.path.exists(path):
        return None
    try:
        df = pd.read_csv(path)
    except Exception:
        return None
    need = {'date', 'open', 'close', 'high', 'low'}
    if df.empty or not need.issubset(set(df.columns)):
        return None
    df = df[['date', 'open', 'close', 'high', 'low']].copy()
    df['date'] = pd.to_datetime(df['date'])
    return df.sort_values('date').drop_duplicates('date').reset_index(drop=True)


# 记录每个标的实际使用的数据源, 写入 summary.meta 供审计
DATA_SOURCE_LOG = {}


def load_data(symbol_code, label):
    """
    数据源优先级: 本地全历史缓存 > westock-data CLI。

    背景: westock-data kline 自 2026-08 起单次最多返回 ~1210 根日线(约 2021-08 起),
    直接使用会把 2014 起的评估窗口静默截断 7 年、丢失 2015-2016 创业板崩盘样本。
    因此优先读取 build_etf_history.py 维护的腾讯前复权全历史缓存;
    仅当本地缺失或过期(>7 自然日)时才降级 westock, 并显式告警。
    两个数据源不混用, 避免前复权基准不同造成价格台阶。
    """
    local = load_local_history(symbol_code)
    source = None
    raw = None

    if local is not None:
        stale_days = (pd.Timestamp(EVAL_END) - local['date'].iloc[-1]).days
        if stale_days <= 7:
            raw, source = local, "local-history"
        else:
            print(f"  [warn] {symbol_code} 本地全历史缓存已过期 {stale_days} 天, 降级 westock")

    if raw is None:
        raw = westock_kline(symbol_code, limit=DATA_LIMIT)
        source = "westock"

    raw = raw[(raw['date'] >= DATA_START) & (raw['date'] <= EVAL_END)].reset_index(drop=True)
    if raw.empty:
        raise RuntimeError(f"{label} ({symbol_code}) 在 {DATA_START}~{EVAL_END} 无数据")

    first, last = raw['date'].iloc[0], raw['date'].iloc[-1]
    DATA_SOURCE_LOG[symbol_code] = {
        "source": source, "rows": int(len(raw)),
        "first": first.strftime('%Y-%m-%d'), "last": last.strftime('%Y-%m-%d'),
    }
    tag = "" if source == "local-history" else f"  [源={source}]"
    print(f"  {label} ({symbol_code}): {len(raw)} 条, "
          f"{first.strftime('%Y-%m-%d')} ~ {last.strftime('%Y-%m-%d')}{tag}")

    # 覆盖度断言: 除 159949(2016-07-22 上市)外, 均应覆盖到 DATA_START 附近
    if symbol_code != "sz159949" and first > pd.Timestamp(DATA_START) + pd.Timedelta(days=45):
        print(f"  [覆盖度告警] {label} 实际起始 {first.strftime('%Y-%m-%d')} "
              f"晚于评估起点 {DATA_START}, 该标的历史被截断!")
    return raw


def ampl20(df):
    return ((df['high'] - df['low']) / df['close'].shift(1)).rolling(20).mean() * 100

def ann_vol(df, window):
    return df['close'].pct_change().rolling(window).std() * np.sqrt(252) * 100


# ═══════════════════════════════════════════════════
# v6 新增: 宏观数据加载与评分
# ═══════════════════════════════════════════════════

def load_macro_data():
    """加载预获取的宏观数据CSV, 返回按日期索引的DataFrame."""
    if not os.path.exists(MACRO_CSV):
        print(f"  WARNING: 宏观数据文件不存在: {MACRO_CSV}")
        print(f"  请先运行: python fetch_macro_data.py")
        return None
    mdf = pd.read_csv(MACRO_CSV, index_col='info_date', parse_dates=True)
    mdf = mdf.sort_index()
    # 前向填充 (月度数据 → 日度)
    mdf = mdf.ffill()
    print(f"  宏观数据: {len(mdf)} 行, {mdf.index.min().strftime('%Y-%m-%d')} ~ {mdf.index.max().strftime('%Y-%m-%d')}")
    return mdf


def compute_macro_score(macro_row, shibor_series, shibor_idx):
    """
    计算宏观得分 (5指标, 各+1/0/-1, 总分-5~+5).
    macro_row: 当日最新的宏观指标行 (Series)
    shibor_series: SHIBOR 1Y 时间序列 (用于计算3个月趋势)
    shibor_idx: 当日在 shibor_series 中的位置
    返回: (score, macro_regime, detail_dict)
    """
    detail = {}
    score = 0

    # 1. PMI
    pmi = macro_row.get('pmi_manu')
    if pmi is not None and not pd.isna(pmi):
        if pmi > MACRO_PMI_BULL:
            detail['pmi'] = +1
        elif pmi < MACRO_PMI_BEAR:
            detail['pmi'] = -1
        else:
            detail['pmi'] = 0
        score += detail['pmi']
    else:
        detail['pmi'] = 0

    # 2. CPI YoY
    cpi = macro_row.get('cpi_yoy')
    if cpi is not None and not pd.isna(cpi):
        if cpi < MACRO_CPI_BULL:
            detail['cpi'] = +1
        elif cpi > MACRO_CPI_BEAR:
            detail['cpi'] = -1
        else:
            detail['cpi'] = 0
        score += detail['cpi']
    else:
        detail['cpi'] = 0

    # 3. PPI YoY
    ppi = macro_row.get('ppi_yoy')
    if ppi is not None and not pd.isna(ppi):
        if ppi > MACRO_PPI_BULL:
            detail['ppi'] = +1
        elif ppi < MACRO_PPI_BEAR:
            detail['ppi'] = -1
        else:
            detail['ppi'] = 0
        score += detail['ppi']
    else:
        detail['ppi'] = 0

    # 4. M2 YoY
    m2 = macro_row.get('m2_yoy')
    if m2 is not None and not pd.isna(m2):
        if m2 > MACRO_M2_BULL:
            detail['m2'] = +1
        elif m2 < MACRO_M2_BEAR:
            detail['m2'] = -1
        else:
            detail['m2'] = 0
        score += detail['m2']
    else:
        detail['m2'] = 0

    # 5. SHIBOR 1Y 趋势 (3个月变化)
    if shibor_series is not None and shibor_idx >= MACRO_SHIBOR_WINDOW:
        cur_shibor = shibor_series.iloc[shibor_idx]
        past_shibor = shibor_series.iloc[shibor_idx - MACRO_SHIBOR_WINDOW]
        if pd.notna(cur_shibor) and pd.notna(past_shibor):
            delta = cur_shibor - past_shibor
            if delta < -MACRO_SHIBOR_THRESHOLD:
                detail['shibor'] = +1
            elif delta > MACRO_SHIBOR_THRESHOLD:
                detail['shibor'] = -1
            else:
                detail['shibor'] = 0
            score += detail['shibor']
            detail['shibor_delta'] = delta
        else:
            detail['shibor'] = 0
    else:
        detail['shibor'] = 0

    # 宏观regime
    if score >= MACRO_SCORE_BULL:
        macro_regime = 'bull'
    elif score <= MACRO_SCORE_BEAR:
        macro_regime = 'bear'
    else:
        macro_regime = 'neutral'

    return score, macro_regime, detail


def detect_regime_combined(close, ma200, ma200_slope, macro_regime):
    """
    技术面 + 宏观面 融合 regime 检测.
    技术面: MA200价格 + 斜率 (同v5)
    宏观面: macro_score 得出的 macro_regime
    融合规则 (宏观只做风控降级, 不做升级):
      技术牛 + 宏观牛/中性 → 牛市
      技术牛 + 宏观熊     → 震荡市 (宏观预警: 通胀/紧缩风险, 降低仓位)
      技术熊 → 熊市 (始终, 不被宏观覆盖)
      技术震 + 宏观熊     → 熊市 (宏观确认下行风险, 提前避险)
      技术震 + 宏观牛/中性 → 震荡市 (不升级为牛: 价格在MA200下方不追涨)
    """
    if close is None or ma200 is None or ma200_slope is None:
        return None

    # 技术面 regime (同v5)
    if close > ma200:
        tech_regime = 'bull'
    elif close < ma200 and ma200_slope < -SLOPE_THRESHOLD:
        tech_regime = 'bear'
    else:
        tech_regime = 'sideways'

    # 宏观面 regime
    m = macro_regime if macro_regime else 'neutral'

    # 融合 (宏观只做风控降级)
    if tech_regime == 'bull':
        if m == 'bear':
            return 'sideways'  # 宏观预警, 降级为震荡
        return 'bull'
    elif tech_regime == 'bear':
        return 'bear'  # 熊市始终为熊
    else:  # sideways
        if m == 'bear':
            return 'bear'  # 宏观确认下行, 降级为熊
        return 'sideways'  # 不升级为牛


# ═══════════════════════════════════════════════════
# export_results (同 v3)
# ═══════════════════════════════════════════════════

def _safef(v):
    if v is None: return None
    try: r = float(v)
    except: return None
    if math.isnan(r) or math.isinf(r): return None
    return r

def _parsets(v):
    if v is None: return None
    if isinstance(v, datetime): return v
    if isinstance(v, date): return datetime.combine(v, datetime.min.time())
    t = str(v).strip()
    if not t: return None
    try: return datetime.fromisoformat(t.replace("Z", "+00:00"))
    except:
        try: return datetime.combine(date.fromisoformat(t), datetime.min.time())
        except: return None

def _slice_eq(eq, s, e):
    sd, ed = _parsets(s), _parsets(e)
    if sd is None and ed is None: return list(eq)
    out = []
    for p in eq:
        pd_ = _parsets(p.get("date"))
        if pd_ is None: continue
        if sd and pd_ < sd: continue
        if ed and pd_ > ed: continue
        out.append(p)
    return out

def _slice_tr(tr, s, e):
    sd, ed = _parsets(s), _parsets(e)
    if sd is None and ed is None: return list(tr)
    out = []
    for t in tr:
        rd = _parsets(t.get("exit_date")) or _parsets(t.get("entry_date"))
        if rd is None: out.append(t); continue
        if sd and rd < sd: continue
        if ed and rd > ed: continue
        out.append(t)
    return out

def _dd_curve(eq):
    dd = []; peak = None
    for p in eq:
        v = _safef(p.get("value"))
        if v is None: continue
        peak = v if peak is None else max(peak, v)
        dp = 0.0 if (peak <= 0 and v == peak) else \
             -100.0 if peak <= 0 else (v/peak - 1.0)*100.0
        dd.append({"date": p["date"], "drawdown_pct": dp})
    return dd

def _ann_factor(eq):
    if len(eq) < 2: return None
    ts = [_parsets(p.get("date")) for p in eq]
    ts = [t for t in ts if t is not None]
    if len(ts) < 2: return None
    pdc = {}
    for t in ts: pdc[t.date()] = pdc.get(t.date(), 0) + 1
    abpd = sum(pdc.values()) / len(pdc)
    deltas = []
    prev = ts[0]
    for t in ts[1:]:
        d = (t - prev).total_seconds()
        if d > 0: deltas.append(d)
        prev = t
    if not deltas: return None
    deltas.sort()
    m = len(deltas)//2
    md = deltas[m] if len(deltas)%2 else (deltas[m-1]+deltas[m])/2.0
    if md <= 0: return None
    md_d = md/86400.0
    if abpd > 1.0: return abpd*252.0
    if md_d <= 2.0: return 252.0
    if md_d <= 10.0: return 52.0
    if md_d <= 40.0: return 12.0
    if md_d <= 120.0: return 4.0
    return 1.0

def _summ(eq, tr, base):
    if not eq or not base:
        return {"total_return_pct":None,"annual_return_pct":None,"max_drawdown_pct":None,
                "sharpe":None,"win_rate_pct":0.0,"total_trades":0}
    fv = _safef(eq[-1].get("value"))
    fv = fv if fv is not None else base
    tr_ = fv/base - 1.0
    rets = []; pv = None
    for p in eq:
        v = _safef(p.get("value"))
        if v is None: continue
        if pv is not None and pv != 0: rets.append(v/pv - 1.0)
        pv = v
    nt = len(tr)
    wc = sum(1 for t in tr if (_safef(t.get("pnl")) or 0)>0)
    wr = wc/nt*100 if nt else 0
    dd = _dd_curve(eq)
    mdd = abs(min(p["drawdown_pct"] for p in dd)) if dd else None
    ap, sp = None, None
    af = _ann_factor(eq)
    if af and rets:
        n = len(rets)
        if 1+tr_ > 0: ap = ((1+tr_)**(af/n)-1)*100
        elif fv <= 0: ap = -100.0
        if n > 1:
            mr = sum(rets)/n
            vr = sum((r-mr)**2 for r in rets)/(n-1)
            std = math.sqrt(vr)
            if std > 0: sp = mr/std*math.sqrt(af)
    return {"total_return_pct":tr_*100,"annual_return_pct":ap,
            "max_drawdown_pct":mdd,"sharpe":sp,"win_rate_pct":wr,"total_trades":nt}


def get_opt_desc():
    opts = []
    if OPT_1_DEFENSE_TIMEOUT: opts.append("①防守超时")
    if OPT_2_REGIME_CONFIRM: opts.append("②Regime确认")
    if OPT_3_TIERED_DEFENSE: opts.append("③分级防守")
    if OPT_4_TRAILING_STOP: opts.append("④移动止盈")
    return "+".join(opts) if opts else "基线(仅2014扩展)"

def export_results(eq, tr, prefix, ic, start=None, end=None, market=None,
                   output_dir=None, strategy_name=None, symbol=None,
                   params=None, daily_snapshot=None):
    od = Path(output_dir) if output_dir else Path.cwd()
    od.mkdir(parents=True, exist_ok=True)
    rs, re = start or (eq[0]["date"] if eq else None), end or (eq[-1]["date"] if eq else None)
    eq2 = _slice_eq(eq, rs, re)
    tr2 = _slice_tr(tr, rs, re)
    wsv = _safef(eq2[0]["value"]) if eq2 else float(ic)
    fv = _safef(eq2[-1]["value"]) if eq2 else float(ic)
    sm = _summ(eq2, tr2, wsv)
    meta = {"strategy_name":strategy_name or "Strategy","symbol":symbol or "",
            "start":rs,"end":re,"initial_cash":float(ic),
            "window_start_value":float(wsv),"final_value":fv,
            "market":market,
            "data_sources":dict(DATA_SOURCE_LOG),
            "generated_at":datetime.now().astimezone().isoformat(timespec="seconds")}
    ep = od / f"{prefix}_equity.csv"
    with ep.open("w",newline="",encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(["date","value"])
        for p in eq2: w.writerow([p["date"], _safef(p.get("value"))])
    tp = od / f"{prefix}_trades.csv"
    fields = ["entry_date","exit_date","side","size","entry_price",
              "exit_price","pnl","pnl_pct","holding_bars","symbol"]
    with tp.open("w",newline="",encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(fields)
        for t in tr2: w.writerow([t.get(k) for k in fields])
    sp = od / f"{prefix}_summary.json"
    out = {"meta": meta, "summary": sm}
    if params is not None:
        out["params"] = params
    if daily_snapshot is not None:
        out["daily_snapshot"] = daily_snapshot
    sp.write_text(json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return {"equity":ep,"trades":tp,"summary":sp}


def compute_baseline(df, symbol_code, eval_start, ic):
    df_eval = df[df['date'] >= eval_start].reset_index(drop=True)
    if len(df_eval) == 0: return [], None
    first_close = df_eval['close'].iloc[0]
    if pd.isna(first_close) or first_close <= 0: return [], None
    baseline = []
    for _, row in df_eval.iterrows():
        v = ic / first_close * row['close']
        baseline.append({'date': str(row['date'])[:10], 'value': round(v, 2)})
    return baseline, (baseline[-1]['value'] / ic - 1) * 100 if baseline else None


# ═══════════════════════════════════════════════════
# Trade record helpers (同 v3)
# ═══════════════════════════════════════════════════

def record_sell(pos, open_px, entry_cost, entry_date, entry_px, exit_date, symbol, th):
    if pos <= 0 or not open_px or open_px <= 0: return 0.0, 0.0
    proc = pos * open_px
    fee = proc * COMMISSION
    net = proc - fee
    pnl = net - (entry_cost or 0)
    pp = pnl/(entry_cost or 1)*100
    try:
        ed = datetime.fromisoformat(exit_date)
        sd = datetime.fromisoformat(entry_date)
        hb = (ed - sd).days
    except: hb = 0
    th.append({
        'entry_date': entry_date, 'exit_date': exit_date,
        'side': 'long', 'size': pos,
        'entry_price': round(entry_px, 4) if entry_px else 0,
        'exit_price': open_px,
        'pnl': round(pnl, 2), 'pnl_pct': round(pp, 2),
        'holding_bars': hb, 'symbol': symbol
    })
    return net, pp


def record_buy(cash, open_px, exit_date):
    if cash <= 0 or not open_px or open_px <= 0: return 0, 0.0, None, None, None
    sz = int(cash / open_px)
    sz = (sz // LOT_SIZE) * LOT_SIZE
    if sz <= 0: return 0, 0.0, None, None, None
    cost = sz * open_px
    fee = cost * COMMISSION
    total = cost + fee
    return sz, total, open_px, exit_date, total


# ═══════════════════════════════════════════════════
# 回测主逻辑
# ═══════════════════════════════════════════════════

def detect_regime(close, ma200, ma200_slope, macro_regime=None):
    """v6: 技术面+宏观面融合的三态检测. 向后兼容: 若 macro_regime=None 则退化为纯技术面."""
    return detect_regime_combined(close, ma200, ma200_slope, macro_regime)


def main():
    print("=" * 65)
    print("风格轮动增强 v10: 2014起+四步优化 宏观融合 + 多ETF风格选择")
    print("  牛市: 159949(成长) vs 510300(大盘) 动量择强 + 510050防守")
    print("  熊市: 纯现金 [继承v6]")
    print("  震荡: 159949 ↔ 159915 配对轮动 [继承v5/v6]")
    print("  宏观: 5指标评分 融合技术面regime [继承v6]")
    print(f"  评估窗口: {EVAL_START} ~ {EVAL_END}")
    print("=" * 65)

    # ---- 1. 加载数据 ----
    print("\n[1/6] 加载数据...")
    # v10: 加载159915作为早期GEM代理 (159949 2016-07才上市)
    gem_proxy = load_data("sz159915", "创业板ETF(代理)")      # 2014-2016期间作为GEM代理
    gem = load_data("sz159949", "创业板50ETF")
    etf50 = load_data("sh510050", "50ETF")
    gem_etf = load_data("sz159915", "创业板ETF")
    hs300 = load_data("sh510300", "沪深300ETF")      # v7 NEW
    div_etf = load_data("sh510880", "红利ETF")        # v7 NEW

    # v6: 加载宏观数据
    print("\n  加载宏观数据...")
    macro_df = load_macro_data()

    # ---- 2. 计算指标 ----
    print("\n[2/6] 计算指标...")
    # v10 FIX: 以代理(159915, 自2011起有数据)为主时间轴, 2016-07-22之前用159915,
    # 之后切换到159949(创业板50), 从而真正把回测窗口延伸到2014 (而非被159949上市日截断).
    proxy_cols = gem_proxy[['date','open','close','high','low']].rename(
        columns={'open':'p_open','close':'p_close','high':'p_high','low':'p_low'})
    gem_cols = gem[['date','open','close','high','low']].rename(
        columns={'open':'g_open','close':'g_close','high':'g_high','low':'g_low'})
    # 主时间轴 = 代理 ∪ 159949 日期并集 (代理更早, 保证从2014起)
    base_dates = (pd.concat([gem_proxy[['date']], gem[['date']]])
                  .drop_duplicates().sort_values('date').reset_index(drop=True))
    df = base_dates.merge(proxy_cols, on='date', how='left').merge(gem_cols, on='date', how='left')
    switch = pd.Timestamp('2016-07-22')
    for col in ['open','close','high','low']:
        df[col] = df['p_'+col].where(df['date'] < switch, df['g_'+col])
    df = df[['date','open','close','high','low']].copy()
    df = df.merge(etf50[['date','open','close','high','low']].rename(
        columns={'open':'f50_open','close':'f50_close',
                 'high':'f50_high','low':'f50_low'}), on='date', how='left')
    df = df.merge(gem_etf[['date','open','close','high','low']].rename(
        columns={'open':'etf_open','close':'etf_close',
                 'high':'etf_high','low':'etf_low'}), on='date', how='left')
    # v7: merge 沪深300 and 红利ETF
    df = df.merge(hs300[['date','open','close','high','low']].rename(
        columns={'open':'hs300_open','close':'hs300_close',
                 'high':'hs300_high','low':'hs300_low'}), on='date', how='left')
    df = df.merge(div_etf[['date','open','close','high','low']].rename(
        columns={'open':'div_open','close':'div_close',
                 'high':'div_high','low':'div_low'}), on='date', how='left')

    # v3 指标
    df['ma200'] = df['close'].rolling(200).mean()
    df['ma60']  = df['close'].rolling(60).mean()
    df['ma200_slope'] = (df['ma200'] - df['ma200'].shift(SLOPE_WINDOW)) / df['ma200'].shift(SLOPE_WINDOW) * 100
    df['ma20']  = df['close'].rolling(20).mean()
    df['amp20'] = ampl20(df)
    df['vol5']  = ann_vol(df, 5)
    df['vol20'] = ann_vol(df, 20)
    df['vol60'] = ann_vol(df, 60)
    df['low60'] = df['close'].rolling(60).min()

    df['amp20_p80'] = df['amp20'].rolling(ADAPTIVE_WINDOW, min_periods=ADAPTIVE_MIN_PERIODS).quantile(ADAPTIVE_PERCENTILE/100)
    df['vol20_p80'] = df['vol20'].rolling(ADAPTIVE_WINDOW, min_periods=ADAPTIVE_MIN_PERIODS).quantile(ADAPTIVE_PERCENTILE/100)
    df['vol60_p80'] = df['vol60'].rolling(ADAPTIVE_WINDOW, min_periods=ADAPTIVE_MIN_PERIODS).quantile(ADAPTIVE_PERCENTILE/100)
    df['amp_threshold'] = np.maximum(df['amp20_p80'], AMP_FLOOR)
    df['vol20_threshold'] = np.maximum(df['vol20_p80'], VOL20_FLOOR)
    df['vol60_threshold'] = np.maximum(df['vol60_p80'], VOL60_FLOOR)

    f50 = etf50[['date','close','high','low']].copy()
    f50['vol20_50'] = ann_vol(f50, 20)
    df = df.merge(f50[['date','vol20_50']], on='date', how='left')

    # v5 新增: 配对轮动指标
    # 20日涨幅差值
    df['ret20_gem'] = df['close'].pct_change(PAIR_RET_WINDOW)
    df['ret20_etf'] = df['etf_close'].pct_change(PAIR_RET_WINDOW)
    df['ret_diff'] = df['ret20_gem'] - df['ret20_etf']
    # z-score
    df['ret_diff_mean'] = df['ret_diff'].rolling(PAIR_Z_WINDOW).mean()
    df['ret_diff_std'] = df['ret_diff'].rolling(PAIR_Z_WINDOW).std()
    df['z_score'] = (df['ret_diff'] - df['ret_diff_mean']) / df['ret_diff_std']
    # 60日滚动相关性
    df['corr_60'] = df['close'].pct_change().rolling(PAIR_Z_WINDOW).corr(df['etf_close'].pct_change())

    # v7 新增: 风格动量 (用于牛市风格选择和熊市防御判断)
    df['ret20_gem'] = df['close'].pct_change(STYLE_MOM_WINDOW) * 100      # 创业板50 20日收益%
    df['ret20_300'] = df['hs300_close'].pct_change(STYLE_MOM_WINDOW) * 100  # 沪深300 20日收益%
    df['ret20_div'] = df['div_close'].pct_change(STYLE_MOM_WINDOW) * 100  # 红利ETF 20日收益% (仅图表展示)

    # 验证相关性
    eval_df = df[df['date'] >= EVAL_START]
    avg_corr = eval_df['corr_60'].mean()
    print(f"  159949 vs 159915 平均相关性: {avg_corr:.3f}")
    print(f"  z-score 范围: [{eval_df['z_score'].min():.2f}, {eval_df['z_score'].max():.2f}]")
    print(f"  |z|>1 占比: {(eval_df['z_score'].abs() > PAIR_Z_ENTRY).mean()*100:.1f}%")
    print(f"  合并后: {len(df)} 条记录")

    # v6: 合并宏观数据并预计算宏观得分
    print("\n  合并宏观数据...")
    if macro_df is not None:
        # 将宏观月度数据 merge 到日频 (asof merge: 每天用最近一次发布的宏观数据)
        macro_df = macro_df.reset_index().rename(columns={'info_date': 'date'})
        macro_df['date'] = pd.to_datetime(macro_df['date'])
        df = pd.merge_asof(df.sort_values('date'), macro_df[['date','cpi_yoy','ppi_yoy','pmi_manu','m2_yoy','shibor_1y']].sort_values('date'),
                           on='date', direction='backward')
        # 预计算每日宏观得分和宏观regime
        shibor_series = df['shibor_1y'].copy()
        macro_scores = []
        macro_regimes = []
        macro_details_list = []
        for idx in range(len(df)):
            row = df.iloc[idx]
            score, mreg, detail = compute_macro_score(row, shibor_series, idx)
            macro_scores.append(score)
            macro_regimes.append(mreg)
            macro_details_list.append(detail)
        df['macro_score'] = macro_scores
        df['macro_regime'] = macro_regimes

        # 统计
        eval_macro = df[df['date'] >= EVAL_START]
        m_bull = (eval_macro['macro_regime'] == 'bull').sum()
        m_bear = (eval_macro['macro_regime'] == 'bear').sum()
        m_neut = (eval_macro['macro_regime'] == 'neutral').sum()
        total = len(eval_macro)
        print(f"  宏观regime分布: 牛{m_bull}天({m_bull/total*100:.0f}%) 熊{m_bear}天({m_bear/total*100:.0f}%) 中性{m_neut}天({m_neut/total*100:.0f}%)")
        print(f"  宏观得分范围: [{eval_macro['macro_score'].min()}, {eval_macro['macro_score'].max()}] 均值: {eval_macro['macro_score'].mean():.2f}")
    else:
        df['macro_score'] = 0
        df['macro_regime'] = 'neutral'
        print("  WARNING: 宏观数据未加载, 退化为纯技术面regime (等同v5)")

    # ---- 3. 计算基线 ----
    print("\n[3/6] 计算买入持有基线...")
    gem_baseline, gem_bh_ret = compute_baseline(gem, 'sz159949', EVAL_START, INITIAL_CASH)
    f50_baseline, f50_bh_ret = compute_baseline(etf50, 'sh510050', EVAL_START, INITIAL_CASH)
    etf_baseline, etf_bh_ret = compute_baseline(gem_etf, 'sz159915', EVAL_START, INITIAL_CASH)
    hs300_baseline, hs300_bh_ret = compute_baseline(hs300, 'sh510300', EVAL_START, INITIAL_CASH)
    div_baseline, div_bh_ret = compute_baseline(div_etf, 'sh510880', EVAL_START, INITIAL_CASH)
    print(f"  创业板50买入持有: {gem_bh_ret:+.1f}%")
    print(f"  50ETF买入持有:     {f50_bh_ret:+.1f}%")
    print(f"  创业板ETF买入持有: {etf_bh_ret:+.1f}%")
    print(f"  沪深300买入持有:   {hs300_bh_ret:+.1f}%")
    print(f"  红利ETF买入持有:   {div_bh_ret:+.1f}%")

    # ---- 4. 回测 ----
    print("\n[4/6] 回测中...")

    # 状态变量
    regime = None
    bull_state = 0              # 0=gem, 1=f50, 2=hs300(v7 NEW)
    bull_style = 'growth'       # v7: 'growth'(159949) or 'broad'(510300) — preferred style
    pending = None              # within-regime switch
    pending_regime_change = None
    lock_days = 0
    consecutive_sig = 0
    clean_streak = 0
    z_pos_streak = 0            # z>entry 连续天数
    z_neg_streak = 0            # z<-entry 连续天数
    side_stopped_out = False    # 震荡市止损后标记
    side_peak = 0.0             # 震荡市持仓峰值
    # v7: 风格轮动追踪
    style_outperf_streak = 0    # 连续超额天数
    # v10: ①防守超时追踪
    defense_counter = 0         # 连续防守天数
    # v10: ②Regime确认追踪
    regime_candidate = None     # {'target': ..., 'streak': ..., ...}
    # v7: 熊市防御
    bear_div_holding = False    # 是否持有红利ETF
    bear_div_check_counter = 0  # 检查计数器

    cash = float(INITIAL_CASH)
    pos_gem = 0; pos_f50 = 0; pos_etf = 0; pos_hs300 = 0; pos_div = 0

    gem_entry_date = gem_entry_px = gem_entry_cost = None
    f50_entry_date = f50_entry_px = f50_entry_cost = None
    etf_entry_date = etf_entry_px = etf_entry_cost = None
    hs300_entry_date = hs300_entry_px = hs300_entry_cost = None  # v7 NEW
    div_entry_date = div_entry_px = div_entry_cost = None        # v7 NEW
    gem_peak = 0.0

    initialized = False

    # 有效价格追踪
    lg_open = lg_close = lg_high = lg_low = None
    lf_open = lf_close = lf_high = lf_low = None
    le_open = le_close = None
    l3_open = l3_close = l3_high = l3_low = None  # v7: 沪深300
    ld_open = ld_close = ld_high = ld_low = None  # v7: 红利ETF

    equity_curve = []
    trade_history = []
    regime_log = []
    regime_days = {'bull': 0, 'bear': 0, 'sideways': 0}
    pair_trades = 0  # 配对轮动交易计数
    # v6: 宏观追踪
    macro_regime_days = {'bull': 0, 'bear': 0, 'neutral': 0}
    tech_vs_combined_diffs = 0  # 技术面与融合面不一致的天数
    daily_regime = []  # 每日regime记录 (用于图表)
    macro_override_count = 0  # 宏观改变技术面regime的次数
    # v7: 风格选择追踪
    style_log = []             # 记录每次风格选择事件
    bull_style_days = {'growth': 0, 'broad': 0, 'defensive': 0}
    bear_div_days = 0          # 熊市持有红利ETF天数
    bear_cash_days = 0         # 熊市纯现金天数

    all_required = ['ma200','ma20','amp20','vol20','vol5','vol60','vol20_50',
                    'amp_threshold','vol20_threshold','vol60_threshold','low60']

    for i in range(len(df)):
        row = df.iloc[i]
        ds = str(row['date'])[:10]

        # ---- 更新有效价格 ----
        go_v = float(row['open']) if pd.notna(row.get('open')) else None
        if go_v is not None and go_v > 0: lg_open = go_v
        gc_v = float(row['close']) if pd.notna(row.get('close')) else None
        if gc_v is not None and gc_v > 0: lg_close = gc_v
        gh_v = float(row['high']) if pd.notna(row.get('high')) else None
        if gh_v is not None and gh_v > 0: lg_high = gh_v
        gl_v = float(row['low']) if pd.notna(row.get('low')) else None
        if gl_v is not None and gl_v > 0: lg_low = gl_v
        fo_v = float(row['f50_open']) if pd.notna(row.get('f50_open')) else None
        if fo_v is not None and fo_v > 0: lf_open = fo_v
        fc_v = float(row['f50_close']) if pd.notna(row.get('f50_close')) else None
        if fc_v is not None and fc_v > 0: lf_close = fc_v
        fh_v = float(row['f50_high']) if pd.notna(row.get('f50_high')) else None
        if fh_v is not None and fh_v > 0: lf_high = fh_v
        fl_v = float(row['f50_low']) if pd.notna(row.get('f50_low')) else None
        if fl_v is not None and fl_v > 0: lf_low = fl_v
        eo_v = float(row['etf_open']) if pd.notna(row.get('etf_open')) else None
        if eo_v is not None and eo_v > 0: le_open = eo_v
        ec_v = float(row['etf_close']) if pd.notna(row.get('etf_close')) else None
        if ec_v is not None and ec_v > 0: le_close = ec_v
        # v7: 沪深300 有效价格
        h3o_v = float(row['hs300_open']) if pd.notna(row.get('hs300_open')) else None
        if h3o_v is not None and h3o_v > 0: l3_open = h3o_v
        h3c_v = float(row['hs300_close']) if pd.notna(row.get('hs300_close')) else None
        if h3c_v is not None and h3c_v > 0: l3_close = h3c_v
        h3h_v = float(row['hs300_high']) if pd.notna(row.get('hs300_high')) else None
        if h3h_v is not None and h3h_v > 0: l3_high = h3h_v
        h3l_v = float(row['hs300_low']) if pd.notna(row.get('hs300_low')) else None
        if h3l_v is not None and h3l_v > 0: l3_low = h3l_v
        # v7: 红利ETF 有效价格
        ldo_v = float(row['div_open']) if pd.notna(row.get('div_open')) else None
        if ldo_v is not None and ldo_v > 0: ld_open = ldo_v
        ldc_v = float(row['div_close']) if pd.notna(row.get('div_close')) else None
        if ldc_v is not None and ldc_v > 0: ld_close = ldc_v
        ldh_v = float(row['div_high']) if pd.notna(row.get('div_high')) else None
        if ldh_v is not None and ldh_v > 0: ld_high = ldh_v
        ldl_v = float(row['div_low']) if pd.notna(row.get('div_low')) else None
        if ldl_v is not None and ldl_v > 0: ld_low = ldl_v

        in_eval = ds >= EVAL_START
        all_ok = all(pd.notna(row[c]) for c in all_required)

        # ---- 预热期: 只更新regime ----
        if not all_ok:
            if pd.notna(row['ma200']) and pd.notna(row['ma200_slope']) and lg_close:
                ma200_v = float(row['ma200'])
                slope_v = float(row['ma200_slope'])
                macro_r = row.get('macro_regime', 'neutral') if pd.notna(row.get('macro_regime')) else 'neutral'
                regime = detect_regime(lg_close, ma200_v, slope_v, macro_r)
            continue

        # ---- 第一天初始化 ----
        if regime is None:
            ma200_v = float(row['ma200'])
            slope_v = float(row['ma200_slope'])
            macro_r = row.get('macro_regime', 'neutral') if pd.notna(row.get('macro_regime')) else 'neutral'
            regime = detect_regime(lg_close, ma200_v, slope_v, macro_r)

        if not initialized and in_eval:
            init_done = False
            if regime == 'bull':
                # v7: 风格选择 — GEM为默认(动量通过即可), HS300为fallback
                go = lg_open or lg_close or 0
                h3o = l3_open or l3_close or 0
                low60_val = float(row['low60'])
                mom_ok = (pd.notna(low60_val) and go > low60_val * (1 + MOMENTUM_THRESHOLD/100))
                ret20_gem_v = float(row['ret20_gem']) if pd.notna(row.get('ret20_gem')) else 0
                ret20_300_v = float(row['ret20_300']) if pd.notna(row.get('ret20_300')) else 0
                # 优先成长(动量通过), 其次大盘均衡(fallback), 最后50ETF防守
                if mom_ok and go > 0 and cash > 0:
                    sz, cost, px, dt, _ = record_buy(cash, go, ds)
                    if sz > 0:
                        cash -= cost; pos_gem = sz
                        gem_entry_date = dt; gem_entry_px = px; gem_entry_cost = cost
                        gem_peak = px; bull_state = 0; bull_style = 'growth'; init_done = True
                        style_log.append({'date': ds, 'event': 'bull_entry', 'style': 'growth',
                                          'ret20_gem': ret20_gem_v, 'ret20_300': ret20_300_v})
                elif h3o > 0 and cash > 0:
                    sz, cost, px, dt, _ = record_buy(cash, h3o, ds)
                    if sz > 0:
                        cash -= cost; pos_hs300 = sz
                        hs300_entry_date = dt; hs300_entry_px = px; hs300_entry_cost = cost
                        bull_state = 2; bull_style = 'broad'; init_done = True
                        style_log.append({'date': ds, 'event': 'bull_entry', 'style': 'broad',
                                          'ret20_gem': ret20_gem_v, 'ret20_300': ret20_300_v})
                if not init_done:
                    fo = lf_open or lf_close or 0
                    sz, cost, px, dt, _ = record_buy(cash, fo, ds)
                    if sz > 0:
                        cash -= cost; pos_f50 = sz
                        f50_entry_date = dt; f50_entry_px = px; f50_entry_cost = cost
                        bull_state = 1; init_done = True
                        style_log.append({'date': ds, 'event': 'bull_entry', 'style': 'defensive',
                                          'ret20_gem': ret20_gem_v, 'ret20_300': ret20_300_v})
            elif regime == 'bear':
                # 熊市纯现金 (红利ETF测试发现不防御, 已移除)
                init_done = True
            elif regime == 'sideways':
                # 震荡市默认现金 (价格在MA200下方, 不默认持有下跌资产)
                init_done = True
            if init_done:
                initialized = True
            gcl = lg_close or 0; fcl = lf_close or 0; ecl = le_close or 0
            h3cl = l3_close or 0; dcl = ld_close or 0
            eqv = cash + pos_gem * gcl + pos_f50 * fcl + pos_etf * ecl + pos_hs300 * h3cl + pos_div * dcl
            equity_curve.append({'date': ds, 'value': round(eqv, 2)})
            if in_eval and regime:
                regime_days[regime] = regime_days.get(regime, 0) + 1
                mr = row.get('macro_regime', 'neutral')
                if pd.notna(mr): macro_regime_days[mr] = macro_regime_days.get(mr, 0) + 1
                daily_regime.append({'date': ds, 'regime': regime, 'macro_regime': mr if pd.notna(mr) else 'neutral',
                                     'macro_score': float(row.get('macro_score', 0)) if pd.notna(row.get('macro_score')) else 0})
            continue

        # ---- 统计regime分布 ----
        if in_eval and regime:
            regime_days[regime] = regime_days.get(regime, 0) + 1
            mr = row.get('macro_regime', 'neutral')
            if pd.notna(mr): macro_regime_days[mr] = macro_regime_days.get(mr, 0) + 1
            daily_regime.append({'date': ds, 'regime': regime, 'macro_regime': mr if pd.notna(mr) else 'neutral',
                                 'macro_score': float(row.get('macro_score', 0)) if pd.notna(row.get('macro_score')) else 0})

        # ---- 执行待切换: regime change ----
        if pending_regime_change is not None:
            target = pending_regime_change.split('_to_')[1]

            if target == 'bear':
                # 全部清仓 (含v7新增的hs300和div)
                if pos_gem > 0 and lg_open and lg_open > 0:
                    net, _ = record_sell(pos_gem, lg_open, gem_entry_cost,
                                         gem_entry_date, gem_entry_px, ds, '159949.SZ', trade_history)
                    cash += net; pos_gem = 0
                    gem_entry_date = gem_entry_px = gem_entry_cost = None; gem_peak = 0.0
                if pos_f50 > 0 and lf_open and lf_open > 0:
                    net, _ = record_sell(pos_f50, lf_open, f50_entry_cost,
                                         f50_entry_date, f50_entry_px, ds, '510050.SH', trade_history)
                    cash += net; pos_f50 = 0
                    f50_entry_date = f50_entry_px = f50_entry_cost = None
                if pos_etf > 0 and le_open and le_open > 0:
                    net, _ = record_sell(pos_etf, le_open, etf_entry_cost,
                                         etf_entry_date, etf_entry_px, ds, '159915.SZ', trade_history)
                    cash += net; pos_etf = 0
                    etf_entry_date = etf_entry_px = etf_entry_cost = None
                if pos_hs300 > 0 and l3_open and l3_open > 0:
                    net, _ = record_sell(pos_hs300, l3_open, hs300_entry_cost,
                                         hs300_entry_date, hs300_entry_px, ds, '510300.SH', trade_history)
                    cash += net; pos_hs300 = 0
                    hs300_entry_date = hs300_entry_px = hs300_entry_cost = None
                # v7: 熊市纯现金 (红利ETF不防御, 已移除)
                bear_div_holding = False
                bear_div_check_counter = 0
                regime = 'bear'

            elif target == 'bull':
                # 卖非bull仓位 (f50, etf, div)
                if pos_f50 > 0 and lf_open and lf_open > 0:
                    net, _ = record_sell(pos_f50, lf_open, f50_entry_cost,
                                         f50_entry_date, f50_entry_px, ds, '510050.SH', trade_history)
                    cash += net; pos_f50 = 0
                    f50_entry_date = f50_entry_px = f50_entry_cost = None
                if pos_etf > 0 and le_open and le_open > 0:
                    net, _ = record_sell(pos_etf, le_open, etf_entry_cost,
                                         etf_entry_date, etf_entry_px, ds, '159915.SZ', trade_history)
                    cash += net; pos_etf = 0
                    etf_entry_date = etf_entry_px = etf_entry_cost = None
                if pos_div > 0 and ld_open and ld_open > 0:
                    net, _ = record_sell(pos_div, ld_open, div_entry_cost,
                                         div_entry_date, div_entry_px, ds, '510880.SH', trade_history)
                    cash += net; pos_div = 0
                    div_entry_date = div_entry_px = div_entry_cost = None
                    bear_div_holding = False
                # v7: 风格选择 — 成长(159949) vs 大盘均衡(510300) 动量择强
                if pos_gem == 0 and pos_hs300 == 0:
                    go = lg_open or lg_close or 0
                    h3o = l3_open or l3_close or 0
                    low60_val = float(row['low60'])
                    mom_ok = (pd.notna(low60_val) and go > low60_val * (1 + MOMENTUM_THRESHOLD/100))
                    ret20_gem_v = float(row['ret20_gem']) if pd.notna(row.get('ret20_gem')) else 0
                    ret20_300_v = float(row['ret20_300']) if pd.notna(row.get('ret20_300')) else 0
                    if mom_ok and go > 0 and ret20_gem_v >= ret20_300_v and cash > 0:
                        sz, cost, px, dt, _ = record_buy(cash, go, ds)
                        if sz > 0:
                            cash -= cost; pos_gem = sz
                            gem_entry_date = dt; gem_entry_px = px; gem_entry_cost = cost
                            gem_peak = px; bull_state = 0; bull_style = 'growth'
                            style_log.append({'date': ds, 'event': 'bull_entry', 'style': 'growth',
                                              'ret20_gem': ret20_gem_v, 'ret20_300': ret20_300_v})
                    elif h3o > 0 and cash > 0:
                        sz, cost, px, dt, _ = record_buy(cash, h3o, ds)
                        if sz > 0:
                            cash -= cost; pos_hs300 = sz
                            hs300_entry_date = dt; hs300_entry_px = px; hs300_entry_cost = cost
                            bull_state = 2; bull_style = 'broad'
                            style_log.append({'date': ds, 'event': 'bull_entry', 'style': 'broad',
                                              'ret20_gem': ret20_gem_v, 'ret20_300': ret20_300_v})
                    elif lf_open and lf_open > 0 and cash > 0:
                        sz, cost, px, dt, _ = record_buy(cash, lf_open, ds)
                        if sz > 0:
                            cash -= cost; pos_f50 = sz
                            f50_entry_date = dt; f50_entry_px = px; f50_entry_cost = cost
                            bull_state = 1
                    else:
                        bull_state = 1
                elif pos_gem > 0:
                    bull_state = 0; bull_style = 'growth'
                elif pos_hs300 > 0:
                    bull_state = 2; bull_style = 'broad'
                style_outperf_streak = 0
                regime = 'bull'

            elif target == 'sideways':
                # 震荡市: 全部清仓转现金 (含v7新增的hs300和div)
                if pos_f50 > 0 and lf_open and lf_open > 0:
                    net, _ = record_sell(pos_f50, lf_open, f50_entry_cost,
                                         f50_entry_date, f50_entry_px, ds, '510050.SH', trade_history)
                    cash += net; pos_f50 = 0
                    f50_entry_date = f50_entry_px = f50_entry_cost = None
                if pos_etf > 0 and le_open and le_open > 0:
                    net, _ = record_sell(pos_etf, le_open, etf_entry_cost,
                                         etf_entry_date, etf_entry_px, ds, '159915.SZ', trade_history)
                    cash += net; pos_etf = 0
                    etf_entry_date = etf_entry_px = etf_entry_cost = None
                if pos_gem > 0 and lg_open and lg_open > 0:
                    net, _ = record_sell(pos_gem, lg_open, gem_entry_cost,
                                         gem_entry_date, gem_entry_px, ds, '159949.SZ', trade_history)
                    cash += net; pos_gem = 0
                    gem_entry_date = gem_entry_px = gem_entry_cost = None; gem_peak = 0.0
                if pos_hs300 > 0 and l3_open and l3_open > 0:
                    net, _ = record_sell(pos_hs300, l3_open, hs300_entry_cost,
                                         hs300_entry_date, hs300_entry_px, ds, '510300.SH', trade_history)
                    cash += net; pos_hs300 = 0
                    hs300_entry_date = hs300_entry_px = hs300_entry_cost = None
                if pos_div > 0 and ld_open and ld_open > 0:
                    net, _ = record_sell(pos_div, ld_open, div_entry_cost,
                                         div_entry_date, div_entry_px, ds, '510880.SH', trade_history)
                    cash += net; pos_div = 0
                    div_entry_date = div_entry_px = div_entry_cost = None
                    bear_div_holding = False
                regime = 'sideways'

            lock_days = LOCK_DAYS
            consecutive_sig = 0; clean_streak = 0
            z_pos_streak = 0; z_neg_streak = 0
            side_stopped_out = False; side_peak = 0.0
            style_outperf_streak = 0
            pending_regime_change = None

        # ---- 执行待切换: within-regime ----
        if pending == "bull_to_f50":
            # v10 ③: 分级防守 — 弱信号保留50%GEM
            sc_local = consecutive_sig  # 保存当前信号强度
            if OPT_3_TIERED_DEFENSE and sc_local < BULL_SIG_TRIGGER * 2 and pos_gem > 0:
                # 弱信号: 完整清仓GEM, 再按比例重建为 50%GEM + 50%50ETF
                # (避免拆分同一物理仓位导致 trades 日志出现 phantom 重复行)
                if pos_gem > 0 and lg_open and lg_open > 0:
                    net, _ = record_sell(pos_gem, lg_open, gem_entry_cost,
                                         gem_entry_date, gem_entry_px, ds, '159949.SZ', trade_history)
                    cash += net; pos_gem = 0
                    gem_entry_date = gem_entry_px = gem_entry_cost = None; gem_peak = 0.0
                if pos_hs300 > 0 and l3_open and l3_open > 0:
                    net, _ = record_sell(pos_hs300, l3_open, hs300_entry_cost,
                                         hs300_entry_date, hs300_entry_px, ds, '510300.SH', trade_history)
                    cash += net; pos_hs300 = 0
                    hs300_entry_date = hs300_entry_px = hs300_entry_cost = None
                # 一半现金买50ETF, 一半买回GEM (fresh 仓位)
                if cash > 0:
                    f50_cash = cash / 2
                    if lf_open and lf_open > 0 and f50_cash > 0:
                        sz, cost, px, dt, _ = record_buy(f50_cash, lf_open, ds)
                        if sz > 0:
                            cash -= cost; pos_f50 = sz
                            f50_entry_date = dt; f50_entry_px = px; f50_entry_cost = cost
                    if lg_open and lg_open > 0 and cash > 0:
                        sz, cost, px, dt, _ = record_buy(cash, lg_open, ds)
                        if sz > 0:
                            cash -= cost; pos_gem = sz
                            gem_entry_date = dt; gem_entry_px = px; gem_entry_cost = cost; gem_peak = px
                pending = None; bull_state = 1
                style_log.append({'date': ds, 'event': 'tiered_defense', 'style': '50%gem+50%f50'})
            else:
                # 强信号或禁用分级: 全清 → 全买50ETF
                if pos_gem > 0 and lg_open and lg_open > 0:
                    net, _ = record_sell(pos_gem, lg_open, gem_entry_cost,
                                         gem_entry_date, gem_entry_px, ds, '159949.SZ', trade_history)
                    cash += net; pos_gem = 0
                    gem_entry_date = gem_entry_px = gem_entry_cost = None; gem_peak = 0.0
                if pos_hs300 > 0 and l3_open and l3_open > 0:
                    net, _ = record_sell(pos_hs300, l3_open, hs300_entry_cost,
                                         hs300_entry_date, hs300_entry_px, ds, '510300.SH', trade_history)
                    cash += net; pos_hs300 = 0
                    hs300_entry_date = hs300_entry_px = hs300_entry_cost = None
                if lf_open and lf_open > 0 and cash > 0:
                    sz, cost, px, dt, _ = record_buy(cash, lf_open, ds)
                    if sz > 0:
                        cash -= cost; pos_f50 = sz
                        f50_entry_date = dt; f50_entry_px = px; f50_entry_cost = cost
                pending = None; bull_state = 1
            lock_days = LOCK_DAYS; consecutive_sig = 0; clean_streak = 0

        elif pending == "bull_to_preferred":
            # v7: 从50ETF防守恢复 — GEM动量通过则回成长, 否则回大盘均衡
            if pos_f50 > 0 and lf_open and lf_open > 0:
                net, _ = record_sell(pos_f50, lf_open, f50_entry_cost,
                                     f50_entry_date, f50_entry_px, ds, '510050.SH', trade_history)
                cash += net; pos_f50 = 0
                f50_entry_date = f50_entry_px = f50_entry_cost = None
            go = lg_open or lg_close or 0
            h3o = l3_open or l3_close or 0
            low60_val = float(row['low60'])
            mom_ok = (pd.notna(low60_val) and go > low60_val * (1 + MOMENTUM_THRESHOLD/100))
            if mom_ok and go > 0 and cash > 0:
                # 先平掉现有GEM(分级防守留下的半仓), 避免与新建仓叠加
                if pos_gem > 0 and lg_open and lg_open > 0:
                    net, _ = record_sell(pos_gem, lg_open, gem_entry_cost,
                                         gem_entry_date, gem_entry_px, ds, '159949.SZ', trade_history)
                    cash += net; pos_gem = 0
                    gem_entry_date = gem_entry_px = gem_entry_cost = None; gem_peak = 0.0
                sz, cost, px, dt, _ = record_buy(cash, go, ds)
                if sz > 0:
                    cash -= cost; pos_gem = sz
                    gem_entry_date = dt; gem_entry_px = px; gem_entry_cost = cost
                    gem_peak = px; bull_state = 0; bull_style = 'growth'
            elif h3o > 0 and cash > 0:
                # GEM动量不足, fallback到大盘均衡
                sz, cost, px, dt, _ = record_buy(cash, h3o, ds)
                if sz > 0:
                    cash -= cost; pos_hs300 = sz
                    hs300_entry_date = dt; hs300_entry_px = px; hs300_entry_cost = cost
                    bull_state = 2; bull_style = 'broad'
            else:
                bull_state = 1
            pending = None
            lock_days = LOCK_DAYS; consecutive_sig = 0; clean_streak = 0

        elif pending == "bull_style_to_300":
            # v7: 已移除主动风格轮动 (仅保留pending定义以防残留引用)
            pending = None

        elif pending == "bull_tiered_recover":
            # v10 ③: 分级防守恢复 — 清掉50ETF, 将半仓GEM重建为满仓GEM
            if pos_f50 > 0 and lf_open and lf_open > 0:
                net, _ = record_sell(pos_f50, lf_open, f50_entry_cost,
                                     f50_entry_date, f50_entry_px, ds, '510050.SH', trade_history)
                cash += net; pos_f50 = 0
                f50_entry_date = f50_entry_px = f50_entry_cost = None
            # 先清掉现有半仓GEM(若有), 再用全部现金整体重建满仓GEM, 避免叠加杠杆
            if pos_gem > 0 and lg_open and lg_open > 0:
                net, _ = record_sell(pos_gem, lg_open, gem_entry_cost,
                                     gem_entry_date, gem_entry_px, ds, '159949.SZ', trade_history)
                cash += net; pos_gem = 0
                gem_entry_date = gem_entry_px = gem_entry_cost = None; gem_peak = 0.0
            go = lg_open or lg_close or 0
            if go > 0 and cash > 0:
                sz, cost, px, dt, _ = record_buy(cash, go, ds)
                if sz > 0:
                    cash -= cost; pos_gem = sz
                    gem_entry_date = dt; gem_entry_px = px; gem_entry_cost = cost
                    gem_peak = px; bull_state = 0; bull_style = 'growth'
            style_log.append({'date': ds, 'event': 'tiered_recover', 'style': 'growth'})
            pending = None
            lock_days = LOCK_DAYS; consecutive_sig = 0; clean_streak = 0

        elif pending == "bull_hs300_to_gem":
            # v7: 从HS300恢复到GEM (GEM动量恢复, 切回成长)
            if pos_hs300 > 0 and l3_open and l3_open > 0:
                net, _ = record_sell(pos_hs300, l3_open, hs300_entry_cost,
                                     hs300_entry_date, hs300_entry_px, ds, '510300.SH', trade_history)
                cash += net; pos_hs300 = 0
                hs300_entry_date = hs300_entry_px = hs300_entry_cost = None
            go = lg_open or lg_close or 0
            if go > 0 and cash > 0:
                sz, cost, px, dt, _ = record_buy(cash, go, ds)
                if sz > 0:
                    cash -= cost; pos_gem = sz
                    gem_entry_date = dt; gem_entry_px = px; gem_entry_cost = cost
                    gem_peak = px; bull_state = 0; bull_style = 'growth'
                    style_log.append({'date': ds, 'event': 'hs300_to_gem', 'style': 'growth'})
            else:
                bull_state = 2; bull_style = 'broad'
            pending = None
            lock_days = LOCK_DAYS; consecutive_sig = 0; clean_streak = 0

        elif pending == "side_to_etf":
            # 震荡市: gem → etf (z-score高, gem超额, 转etf)
            if pos_gem > 0 and lg_open and lg_open > 0:
                net, _ = record_sell(pos_gem, lg_open, gem_entry_cost,
                                     gem_entry_date, gem_entry_px, ds, '159949.SZ', trade_history)
                cash += net; pos_gem = 0
                gem_entry_date = gem_entry_px = gem_entry_cost = None; gem_peak = 0.0
            if le_open and le_open > 0 and cash > 0:
                sz, cost, px, dt, _ = record_buy(cash, le_open, ds)
                if sz > 0:
                    cash -= cost; pos_etf = sz
                    etf_entry_date = dt; etf_entry_px = px; etf_entry_cost = cost
            pending = None
            lock_days = LOCK_DAYS; z_pos_streak = 0; z_neg_streak = 0
            side_peak = 0.0
            pair_trades += 1

        elif pending == "side_to_gem":
            # 震荡市: etf → gem (z-score低或回归中性)
            if pos_etf > 0 and le_open and le_open > 0:
                net, _ = record_sell(pos_etf, le_open, etf_entry_cost,
                                     etf_entry_date, etf_entry_px, ds, '159915.SZ', trade_history)
                cash += net; pos_etf = 0
                etf_entry_date = etf_entry_px = etf_entry_cost = None
            if lg_open and lg_open > 0 and cash > 0:
                sz, cost, px, dt, _ = record_buy(cash, lg_open, ds)
                if sz > 0:
                    cash -= cost; pos_gem = sz
                    gem_entry_date = dt; gem_entry_px = px; gem_entry_cost = cost
                    gem_peak = px
            pending = None
            lock_days = LOCK_DAYS; z_pos_streak = 0; z_neg_streak = 0
            side_peak = 0.0
            pair_trades += 1

        elif pending == "side_to_cash":
            # 震荡市止损: 全部清仓
            if pos_gem > 0 and lg_open and lg_open > 0:
                net, _ = record_sell(pos_gem, lg_open, gem_entry_cost,
                                     gem_entry_date, gem_entry_px, ds, '159949.SZ', trade_history)
                cash += net; pos_gem = 0
                gem_entry_date = gem_entry_px = gem_entry_cost = None; gem_peak = 0.0
            if pos_etf > 0 and le_open and le_open > 0:
                net, _ = record_sell(pos_etf, le_open, etf_entry_cost,
                                     etf_entry_date, etf_entry_px, ds, '159915.SZ', trade_history)
                cash += net; pos_etf = 0
                etf_entry_date = etf_entry_px = etf_entry_cost = None
            pending = None
            side_stopped_out = True
            lock_days = LOCK_DAYS; z_pos_streak = 0; z_neg_streak = 0

        elif pending == "side_z_exit":
            # z-score回归中性: 平仓回现金 (不触发冷却, 可再进场)
            if pos_gem > 0 and lg_open and lg_open > 0:
                net, _ = record_sell(pos_gem, lg_open, gem_entry_cost,
                                     gem_entry_date, gem_entry_px, ds, '159949.SZ', trade_history)
                cash += net; pos_gem = 0
                gem_entry_date = gem_entry_px = gem_entry_cost = None; gem_peak = 0.0
            if pos_etf > 0 and le_open and le_open > 0:
                net, _ = record_sell(pos_etf, le_open, etf_entry_cost,
                                     etf_entry_date, etf_entry_px, ds, '159915.SZ', trade_history)
                cash += net; pos_etf = 0
                etf_entry_date = etf_entry_px = etf_entry_cost = None
            pending = None
            lock_days = LOCK_DAYS; z_pos_streak = 0; z_neg_streak = 0
            side_peak = 0.0
            pair_trades += 1

        # ---- 信号评估 ----
        ma200_v = float(row['ma200']) if pd.notna(row['ma200']) else None
        slope_v = float(row['ma200_slope']) if pd.notna(row['ma200_slope']) else None
        new_regime = detect_regime(lg_close, ma200_v, slope_v,
                                    row.get('macro_regime', 'neutral') if pd.notna(row.get('macro_regime')) else 'neutral') or regime

        if new_regime != regime and in_eval and pending_regime_change is None:
            # v10: ② Regime确认期 — 需要连续N天同侧才切换
            if OPT_2_REGIME_CONFIRM:
                # 累积同向天数
                if regime_candidate is None:
                    regime_candidate = {'target': new_regime, 'streak': 1, 'start_ds': ds}
                else:
                    existing = regime_candidate
                    if existing['target'] == new_regime:
                        existing['streak'] += 1
                        if existing['streak'] >= REGIME_CONFIRM_DAYS:
                            # 确认切换
                            pending_regime_change = f"{regime}_to_{new_regime}"
                            regime_log.append({'date': ds, 'from': regime, 'to': new_regime,
                               'close': lg_close, 'ma200': ma200_v, 'slope': slope_v,
                               'macro_regime': row.get('macro_regime', 'neutral') if pd.notna(row.get('macro_regime')) else 'neutral',
                               'macro_score': float(row.get('macro_score', 0)) if pd.notna(row.get('macro_score')) else 0})
                            # 重置确认计数器
                            regime_candidate = None
                    else:
                        existing['target'] = new_regime
                        existing['streak'] = 1
                        existing['start_ds'] = ds
            else:
                # 原始: 即时切换
                pending_regime_change = f"{regime}_to_{new_regime}"
                regime_log.append({'date': ds, 'from': regime, 'to': new_regime,
                               'close': lg_close, 'ma200': ma200_v, 'slope': slope_v,
                               'macro_regime': row.get('macro_regime', 'neutral') if pd.notna(row.get('macro_regime')) else 'neutral',
                               'macro_score': float(row.get('macro_score', 0)) if pd.notna(row.get('macro_score')) else 0})

        elif new_regime == regime and pending is None and pending_regime_change is None:
            # ---- 锁定倒计时 ----
            if lock_days > 0:
                lock_days -= 1

            if regime == 'bull':
                # === 牛市逻辑 (v3继承 + v7风格轮动) ===
                dd_trigger = False
                if bull_state == 0 and pos_gem > 0 and lg_close and gem_peak > 0:
                    if lg_close < gem_peak * (1 - DRAWDOWN_STOP_PCT/100):
                        dd_trigger = True
                    # v10 ④: 移动止盈 — 从峰值回撤超过TRAILING_STOP_PCT即退出
                    if OPT_4_TRAILING_STOP:
                        trailing_pct = (lg_close / gem_peak - 1) * 100
                        if trailing_pct <= -TRAILING_STOP_PCT:
                            # 触发移动止损 → 全部清仓, 等待下一次bull entry
                            dd_trigger = True
                    gem_peak = max(gem_peak, lg_close)

                if lock_days == 0:
                    sc = 0
                    a = float(row['amp20']); v20 = float(row['vol20'])
                    v5 = float(row['vol5']); v60 = float(row['vol60'])
                    v50 = float(row['vol20_50'])
                    ath = float(row['amp_threshold'])
                    v20th = float(row['vol20_threshold'])
                    v60th = float(row['vol60_threshold'])
                    if pd.notna(a) and pd.notna(ath) and a > ath: sc += 1
                    if pd.notna(v20) and pd.notna(v50) and v20 > v20th and v50 > BULL_VOL20_50_CROSS: sc += 1
                    if pd.notna(v5) and pd.notna(v20) and v5 > v20: sc += 1
                    if pd.notna(v60) and pd.notna(v60th) and v60 > v60th: sc += 1

                    if bull_state == 0:
                        # 持成长: 波动率防守检查
                        if dd_trigger:
                            pending = "bull_to_f50"
                            consecutive_sig = 0
                        elif sc >= BULL_SIG_TRIGGER:
                            consecutive_sig += 1
                            if consecutive_sig >= CONSECUTIVE_SIG_DAYS:
                                pending = "bull_to_f50"
                                consecutive_sig = 0
                        else:
                            consecutive_sig = 0

                    elif bull_state == 1:
                        # v10 ① ③: 持50ETF防守 — 超时强制检查 + 分级恢复
                        if OPT_1_DEFENSE_TIMEOUT:
                            # 追踪防守天数
                            defense_counter += 1
                            def_days = defense_counter
                        else:
                            def_days = 0
                        
                        if sc > 0:
                            clean_streak = 0
                        else:
                            clean_streak += 1
                        
                        # 正常恢复: clean days足够
                        if clean_streak >= CLEAN_DAYS_NEEDED:
                            pending = "bull_to_preferred"
                            clean_streak = 0
                            if OPT_1_DEFENSE_TIMEOUT:
                                defense_counter = 0
                        # ① 超时强制恢复: 防守>MAX_DEFENSE_DAYS且GEM创20日新高
                        elif OPT_1_DEFENSE_TIMEOUT and def_days > MAX_DEFENSE_DAYS and sc == 0:
                            ma20_v = float(row['ma20']) if pd.notna(row.get('ma20')) else None
                            if ma20_v and lg_close and lg_close > ma20_v:
                                # GEM在MA20上方 → 分级恢复
                                if OPT_3_TIERED_DEFENSE:
                                    pending = "bull_tiered_recover"
                                else:
                                    pending = "bull_to_preferred"
                                clean_streak = 0
                                defense_counter = 0

                    elif bull_state == 2:
                        # v7: 持大盘均衡(510300) — 波动率防守 + 检查GEM恢复
                        if sc >= BULL_SIG_TRIGGER:
                            consecutive_sig += 1
                            if consecutive_sig >= CONSECUTIVE_SIG_DAYS:
                                pending = "bull_to_f50"
                                consecutive_sig = 0
                        else:
                            consecutive_sig = 0
                            # 检查GEM动量是否恢复 — 若通过则切回成长
                            if sc == 0:
                                clean_streak += 1
                                if clean_streak >= CLEAN_DAYS_NEEDED:
                                    go_check = lg_close or 0
                                    low60_val = float(row['low60'])
                                    mom_ok = (pd.notna(low60_val) and go_check > low60_val * (1 + MOMENTUM_THRESHOLD/100))
                                    if mom_ok:
                                        pending = "bull_hs300_to_gem"
                                    clean_streak = 0
                            else:
                                clean_streak = 0

                elif dd_trigger and bull_state == 0:
                    pending = "bull_to_f50"
                    consecutive_sig = 0

                # v7: 统计牛市风格天数
                if in_eval:
                    if bull_state == 0: bull_style_days['growth'] += 1
                    elif bull_state == 1: bull_style_days['defensive'] += 1
                    elif bull_state == 2: bull_style_days['broad'] += 1

            elif regime == 'bear':
                # 熊市纯现金 (v7: 红利ETF测试发现不防御, 已移除)
                pass

            elif regime == 'sideways':
                # === 震荡市: 配对轮动 ===
                # 止损后冷却: 不再交易, 等regime变化
                if side_stopped_out:
                    pass
                elif lock_days == 0:
                    # 回撤止损检查
                    side_dd_trigger = False
                    if pos_gem > 0 and lg_close:
                        if side_peak == 0.0:
                            side_peak = lg_close
                        elif lg_close < side_peak * (1 - SIDE_DD_STOP_PCT/100):
                            side_dd_trigger = True
                        side_peak = max(side_peak, lg_close)
                    elif pos_etf > 0 and le_close:
                        if side_peak == 0.0:
                            side_peak = le_close
                        elif le_close < side_peak * (1 - SIDE_DD_STOP_PCT/100):
                            side_dd_trigger = True
                        side_peak = max(side_peak, le_close)

                    if side_dd_trigger:
                        pending = "side_to_cash"
                    else:
                        z = float(row['z_score']) if pd.notna(row.get('z_score')) else float('nan')
                        corr = float(row['corr_60']) if pd.notna(row.get('corr_60')) else 0.0
                        # 趋势过滤: MA200下行时不进场 (避免下跌趋势中抄底)
                        slope_ok = (slope_v is not None and not pd.isna(slope_v) and slope_v > 0)

                        if pd.isna(z) or abs(z) > 10:
                            z_pos_streak = 0; z_neg_streak = 0
                        elif corr < PAIR_CORR_MIN:
                            z_pos_streak = 0; z_neg_streak = 0
                        elif not slope_ok:
                            z_pos_streak = 0; z_neg_streak = 0
                        else:
                            if z > PAIR_Z_ENTRY:
                                z_pos_streak += 1; z_neg_streak = 0
                                if z_pos_streak >= PAIR_CONSECUTIVE and pos_etf == 0:
                                    # z高: gem超额 → 买159915(从cash或从gem切换)
                                    pending = "side_to_etf"
                                    z_pos_streak = 0
                            elif z < -PAIR_Z_ENTRY:
                                z_neg_streak += 1; z_pos_streak = 0
                                if z_neg_streak >= PAIR_CONSECUTIVE and pos_gem == 0:
                                    # z低: etf超额 → 买159949(从cash或从etf切换)
                                    pending = "side_to_gem"
                                    z_neg_streak = 0
                            elif abs(z) < PAIR_Z_EXIT:
                                z_pos_streak = 0; z_neg_streak = 0
                                if pos_etf > 0 or pos_gem > 0:
                                    # z回归中性 → 平仓回现金 (不触发冷却)
                                    pending = "side_z_exit"
                            else:
                                z_pos_streak = 0; z_neg_streak = 0
                else:
                    # 锁仓期内仍更新峰值
                    if pos_gem > 0 and lg_close:
                        if side_peak == 0.0:
                            side_peak = lg_close
                        side_peak = max(side_peak, lg_close)
                    elif pos_etf > 0 and le_close:
                        if side_peak == 0.0:
                            side_peak = le_close
                        side_peak = max(side_peak, le_close)

        # ---- 记录权益 ----
        gcl = lg_close or 0; fcl = lf_close or 0; ecl = le_close or 0
        h3cl = l3_close or 0; dcl = ld_close or 0
        eqv = cash + pos_gem * gcl + pos_f50 * fcl + pos_etf * ecl + pos_hs300 * h3cl + pos_div * dcl
        equity_curve.append({'date': ds, 'value': round(eqv, 2)})

    # ---- 强制平仓 ----
    last_ds = str(df['date'].iloc[-1])[:10]
    if pos_gem > 0 and lg_close and lg_close > 0:
        net, _ = record_sell(pos_gem, lg_close, gem_entry_cost,
                             gem_entry_date, gem_entry_px, last_ds, '159949.SZ', trade_history)
        cash += net; pos_gem = 0
    if pos_f50 > 0 and lf_close and lf_close > 0:
        net, _ = record_sell(pos_f50, lf_close, f50_entry_cost,
                             f50_entry_date, f50_entry_px, last_ds, '510050.SH', trade_history)
        cash += net; pos_f50 = 0
    if pos_etf > 0 and le_close and le_close > 0:
        net, _ = record_sell(pos_etf, le_close, etf_entry_cost,
                             etf_entry_date, etf_entry_px, last_ds, '159915.SZ', trade_history)
        cash += net; pos_etf = 0
    if pos_hs300 > 0 and l3_close and l3_close > 0:
        net, _ = record_sell(pos_hs300, l3_close, hs300_entry_cost,
                             hs300_entry_date, hs300_entry_px, last_ds, '510300.SH', trade_history)
        cash += net; pos_hs300 = 0
    if pos_div > 0 and ld_close and ld_close > 0:
        net, _ = record_sell(pos_div, ld_close, div_entry_cost,
                             div_entry_date, div_entry_px, last_ds, '510880.SH', trade_history)
        cash += net; pos_div = 0

    # ---- 5. 导出 ----
    print("\n[5/6] 导出结果...")
    export_results(
        equity_curve, trade_history, PREFIX, INITIAL_CASH,
        start=EVAL_START, end=EVAL_END, market="china_a",
        output_dir=str(OUTPUT_DIR),
        strategy_name=f"风格轮动增强v10 (2014起+优化等级{LEVEL}: {get_opt_desc()})",
        symbol="159949.SZ / 510300.SH / 510050.SH / 510880.SH / 159915.SZ",
        params=collect_params(),
        daily_snapshot=load_daily_snapshot(str(OUTPUT_DIR)),
    )

    # v6: 保存每日regime和宏观得分 (用于图表)
    import csv as _csv
    regime_path = OUTPUT_DIR / f"{PREFIX}_regime.csv"
    with open(regime_path, 'w', newline='', encoding='utf-8') as f:
        w = _csv.writer(f)
        w.writerow(['date', 'regime', 'macro_regime', 'macro_score'])
        for d in daily_regime:
            w.writerow([d['date'], d['regime'], d['macro_regime'], d['macro_score']])
    # 保存regime切换日志
    regime_log_path = OUTPUT_DIR / f"{PREFIX}_regime_log.csv"
    with open(regime_log_path, 'w', newline='', encoding='utf-8') as f:
        w = _csv.writer(f)
        w.writerow(['date', 'from', 'to', 'close', 'ma200', 'slope', 'macro_regime', 'macro_score'])
        for r in regime_log:
            w.writerow([r['date'], r['from'], r['to'], r.get('close',''), r.get('ma200',''),
                        r.get('slope',''), r.get('macro_regime',''), r.get('macro_score','')])
    print(f"  regime数据已保存: {regime_path}")
    print(f"  regime日志已保存: {regime_log_path}")

    # v7: 保存风格选择日志
    import csv as _csv2
    style_path = OUTPUT_DIR / f"{PREFIX}_style_log.csv"
    with open(style_path, 'w', newline='', encoding='utf-8') as f:
        w = _csv2.writer(f)
        w.writerow(['date', 'event', 'style', 'ret20_gem', 'ret20_300', 'ret20_div'])
        for s in style_log:
            w.writerow([s.get('date',''), s.get('event',''), s.get('style', s.get('from','')+'->'+s.get('to','')),
                        s.get('ret20_gem',''), s.get('ret20_300',''), s.get('ret20_div','')])
    print(f"  风格日志已保存: {style_path} ({len(style_log)} 条)")

    sm = json.load(open(OUTPUT_DIR / f"{PREFIX}_summary.json"))
    s = sm['summary']
    total_days = sum(regime_days.values())
    print(f"\n  ┌──────────────────────────────────────────────────────┐")
    print(f"  │           策略 v10 风格轮动增强 (2014起, 优化等级{LEVEL})                   │")
    print(f"  ├──────────────────────────────────────────────────────┤")
    print(f"  │ 策略总收益:   {s['total_return_pct']:+.1f}%")
    print(f"  │ 策略年化:     {s['annual_return_pct']:+.1f}%")
    print(f"  │ 策略最大回撤: {s['max_drawdown_pct']:.1f}%")
    shrp = f"{s['sharpe']:.2f}" if s['sharpe'] is not None else "--"
    print(f"  │ 策略Sharpe:   {shrp}")
    print(f"  │ 策略胜率:     {s['win_rate_pct']:.1f}%")
    print(f"  │ 策略交易:     {s['total_trades']} 笔 (配对轮动: {pair_trades})")
    print(f"  ├──────────────────────────────────────────────────────┤")
    print(f"  │ 创业板50买入持有: {gem_bh_ret:+.1f}%")
    print(f"  │ 50ETF买入持有:     {f50_bh_ret:+.1f}%")
    print(f"  │ 创业板ETF买入持有: {etf_bh_ret:+.1f}%")
    print(f"  │ 沪深300买入持有:   {hs300_bh_ret:+.1f}%")
    print(f"  │ 红利ETF买入持有:   {div_bh_ret:+.1f}%")
    print(f"  ├──────────────────────────────────────────────────────┤")
    if total_days > 0:
        print(f"  │ 三态分布: 牛市 {regime_days['bull']}天 ({regime_days['bull']/total_days*100:.0f}%) | "
              f"熊市 {regime_days['bear']}天 ({regime_days['bear']/total_days*100:.0f}%) | "
              f"震荡 {regime_days['sideways']}天 ({regime_days['sideways']/total_days*100:.0f}%)")
    bull_total = sum(bull_style_days.values())
    if bull_total > 0:
        print(f"  │ 牛市风格: 成长{bull_style_days['growth']}天({bull_style_days['growth']/bull_total*100:.0f}%) "
              f"大盘均衡{bull_style_days['broad']}天({bull_style_days['broad']/bull_total*100:.0f}%) "
              f"防守{bull_style_days['defensive']}天({bull_style_days['defensive']/bull_total*100:.0f}%)")
    bear_total = bear_div_days + bear_cash_days
    if bear_total > 0:
        print(f"  │ 熊市防御: 红利ETF{bear_div_days}天({bear_div_days/bear_total*100:.0f}%) "
              f"现金{bear_cash_days}天({bear_cash_days/bear_total*100:.0f}%)")
    macro_total = sum(macro_regime_days.values())
    if macro_total > 0:
        print(f"  │ 宏观分布: 牛{macro_regime_days['bull']}天({macro_regime_days['bull']/macro_total*100:.0f}%) "
              f"熊{macro_regime_days['bear']}天({macro_regime_days['bear']/macro_total*100:.0f}%) "
              f"中性{macro_regime_days['neutral']}天({macro_regime_days['neutral']/macro_total*100:.0f}%)")
    print(f"  │ 三态切换: {len(regime_log)} 次 | 风格事件: {len(style_log)} 次")
    print(f"  └──────────────────────────────────────────────────────┘")

    return equity_curve, trade_history, gem_baseline, f50_baseline, etf_baseline, \
           gem_bh_ret, f50_bh_ret, etf_bh_ret, regime_days, pair_trades, \
           macro_regime_days, daily_regime, regime_log, style_log, \
           bull_style_days, bear_div_days, bear_cash_days, hs300_bh_ret, div_bh_ret


if __name__ == "__main__":
    main()
