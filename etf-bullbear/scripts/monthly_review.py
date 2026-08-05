"""
ETF 牛熊择时 · 月度观点生成器 (monthly_review.py)
============================================
独立于回测的「当前状态」诊断: 用 westock 实时行情 + 本地宏观CSV,
计算当下市场所处的 regime (牛/熊/震荡), 输出:
  - 行情趋势判断 (trend_read)
  - 本月投资策略 (strategy_view)  —— 基于 +① 防守超时 档位的模型信号
  - 关键观察信号 + 风险点
落盘 monthly_review.json, 并打印可读中文观点。

用法:
  python monthly_review.py
"""

import subprocess, re, json, os, sys
from datetime import datetime
from pathlib import Path
import pandas as pd
import numpy as np

NODE_EXE = os.environ.get("NODE_EXE", "C:/Users/loveweiyi/.workbuddy/binaries/node/versions/22.22.2/node.exe")
WESTOCK_JS = os.environ.get("WESTOCK_JS", "C:/Users/loveweiyi/.workbuddy/plugins/marketplaces/experts/plugins/strategy-backtest-expert/skills/westock-data/scripts/index.js")
# CI 覆盖: ETF_HISTORY_DIR 环境变量指向仓库内 data/etf_history (本地兜底缓存)
ETF_HISTORY_DIR = os.environ.get("ETF_HISTORY_DIR", "D:/quant_data/etf_history")
MACRO_CSV = os.environ.get("MACRO_CSV", os.path.join(os.path.dirname(os.path.abspath(__file__)), "macro_data.csv"))

# ---- 输出目录解析: 环境变量 ETF_OUTPUT_DIR > --output-dir 参数 > 桌面 ----
_output_dir = os.environ.get("ETF_OUTPUT_DIR", "")
if not _output_dir:
    for i, a in enumerate(sys.argv[1:], 1):
        if a == "--output-dir" and i + 1 < len(sys.argv):
            _output_dir = sys.argv[i + 1]
            break
OUTPUT_DIR = Path(_output_dir) if _output_dir else Path(os.path.expanduser("~/Desktop"))
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

SLOPE_WINDOW = 20
SLOPE_THRESHOLD = 0.5        # MA200斜率阈值(%): |slope|<此值=震荡
MOMENTUM_LOW_WINDOW = 60
MOMENTUM_THRESHOLD = 10.0    # +① 防守超时: GEM 60日动量阈值
MAX_DEFENSE_DAYS = 30

# ETF 池
ETFS = {
    "159949.SZ": "创业板50ETF",
    "159915.SZ": "创业板ETF",
    "510300.SH": "沪深300ETF",
    "510050.SH": "上证50ETF",
    "510880.SH": "红利ETF",
}


def _parse_kline(stdout):
    output = (stdout or "").strip()
    if not output or "数据为空" in output:
        return None
    lines = output.split('\n')
    sep = False
    data_lines = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if line.startswith('|') and '---' in line:
            sep = True
            continue
        if sep and line.startswith('|') and '日期' not in line:
            data_lines.append(line)
    if not data_lines:
        return None
    rows = []
    for dl in data_lines:
        cells = [c.strip() for c in dl.split('|')[1:-1]]
        if len(cells) < 6:
            continue
        rows.append({
            'date': cells[0],
            'open': float(cells[1].replace(',', '')),
            'close': float(cells[2].replace(',', '')),
            'high': float(cells[3].replace(',', '')),
            'low': float(cells[4].replace(',', '')),
        })
    if not rows:
        return None
    df = pd.DataFrame(rows)
    df['date'] = pd.to_datetime(df['date'])
    df = df.sort_values('date').reset_index(drop=True)
    return df


def westock_kline(code, limit=320, retries=6, sleep=4):
    """带退避重试的行情拉取. 接口间歇性返回'数据为空', 多试几次即可恢复.
    CI 环境腾讯接口可能不可达: 设置 ETF_OFFLINE=1 可跳过网络直读本地产存;
    否则最终失败前回退到本地全历史缓存 (ETF_HISTORY_DIR)."""
    import time as _t
    # 离线模式 (CI / 网络不可达): 直接走本地全历史缓存, 避免无效重试等待
    if os.environ.get("ETF_OFFLINE") == "1":
        local = local_kline(code, limit)
        if local is not None:
            return local
        raise RuntimeError(f"offline mode: local cache missing for {code}")
    cmd = [NODE_EXE, WESTOCK_JS, "kline", code, "--period", "day",
           "--limit", str(limit), "--fq", "qfq"]
    last_err = None
    for attempt in range(retries):
        try:
            result = subprocess.run(cmd, capture_output=True, text=True)
            df = _parse_kline(result.stdout)
            if df is None or len(df) == 0:
                raise RuntimeError("empty/数据为空")
            return df
        except Exception as e:
            last_err = e
            print(f"  [retry {attempt+1}/{retries}] kline {code}: {e}")
            _t.sleep(sleep + attempt)
    # 终极兜底: 本地全历史缓存
    local = local_kline(code, limit)
    if local is not None:
        print(f"  [fallback] kline {code} 使用本地缓存 ({len(local)} 行)")
        return local
    raise RuntimeError(f"westock kline {code} failed after {retries} attempts: {last_err}")


def local_kline(code, limit=320):
    """兜底: 从本地全历史缓存读取 (ETF_HISTORY_DIR/{code}.csv). 列同 westock_kline 输出."""
    path = os.path.join(ETF_HISTORY_DIR, f"{code}.csv")
    if not os.path.exists(path):
        return None
    try:
        df = pd.read_csv(path)
        df['date'] = pd.to_datetime(df['date'])
        df = df.sort_values('date').reset_index(drop=True)
        if limit:
            df = df.tail(limit)
        return df
    except Exception as e:
        print(f"  [local_kline] {code} 读取失败: {e}")
        return None


def indicators(df):
    """返回含 ma200 / ma200_slope / ret20 / ret60 / ann_vol 的末行字典."""
    close = df['close']
    df = df.copy()
    df['ma200'] = close.rolling(200).mean()
    df['ma200_slope'] = (df['ma200'] - df['ma200'].shift(SLOPE_WINDOW)) / df['ma200'].shift(SLOPE_WINDOW) * 100
    df['ret20'] = close.pct_change(20) * 100
    df['ret60'] = close.pct_change(60) * 100
    df['ann_vol'] = close.pct_change().rolling(20).std() * np.sqrt(252) * 100
    last = df.iloc[-1]
    return {
        'date': str(last['date'].date()),
        'close': round(float(last['close']), 4),
        'ma200': None if pd.isna(last['ma200']) else round(float(last['ma200']), 4),
        'slope': None if pd.isna(last['ma200_slope']) else round(float(last['ma200_slope']), 3),
        'ret20': None if pd.isna(last['ret20']) else round(float(last['ret20']), 2),
        'ret60': None if pd.isna(last['ret60']) else round(float(last['ret60']), 2),
        'ann_vol': None if pd.isna(last['ann_vol']) else round(float(last['ann_vol']), 2),
        'above_ma200': (False if pd.isna(last['ma200']) else bool(last['close'] > last['ma200'])),
    }


def load_macro():
    if not os.path.exists(MACRO_CSV):
        return None
    m = pd.read_csv(MACRO_CSV, index_col='info_date', parse_dates=True).sort_index().ffill()
    if len(m) < 5:
        return None
    return m


def macro_score(m):
    """简化版: 用最新一行 + shibor_1y 63日趋势算分 (范围 -5~+5)."""
    row = m.iloc[-1]
    score = 0
    detail = {}
    pmi = row.get('pmi_manu')
    if pd.notna(pmi):
        score += 1 if pmi > 51.0 else (-1 if pmi < 49.0 else 0)
        detail['pmi'] = float(pmi)
    cpi = row.get('cpi_yoy')
    if pd.notna(cpi):
        score += 1 if cpi < 2.0 else (-1 if cpi > 3.0 else 0)
        detail['cpi'] = float(cpi)
    ppi = row.get('ppi_yoy')
    if pd.notna(ppi):
        score += 1 if ppi > 1.0 else (-1 if ppi < -2.0 else 0)
        detail['ppi'] = float(ppi)
    m2 = row.get('m2_yoy')
    if pd.notna(m2):
        score += 1 if m2 > 11.0 else (-1 if m2 < 8.0 else 0)
        detail['m2'] = float(m2)
    if 'shibor_1y' in m.columns and len(m) >= 63:
        cur = m['shibor_1y'].iloc[-1]
        past = m['shibor_1y'].iloc[-63]
        if pd.notna(cur) and pd.notna(past):
            d = cur - past
            score += -1 if d > 0.15 else (1 if d < -0.15 else 0)
            detail['shibor_1y'] = round(float(cur), 4)
            detail['shibor_delta'] = round(float(d), 4)
    regime = 'bull' if score >= 2 else ('bear' if score <= -2 else 'neutral')
    return score, regime, detail


def detect_regime(close, ma200, slope, macro_regime):
    if close is None or ma200 is None or slope is None:
        return None
    if close > ma200:
        tech = 'bull'
    elif close < ma200 and slope < -SLOPE_THRESHOLD:
        tech = 'bear'
    else:
        tech = 'sideways'
    m = macro_regime or 'neutral'
    if tech == 'bull':
        return 'sideways' if m == 'bear' else 'bull'
    if tech == 'bear':
        return 'bear'
    return 'bear' if m == 'bear' else 'sideways'


def build_view(gem, hs300, f50, div, macro):
    """构造中文行情趋势判断 + 本月投资策略."""
    as_of = gem['date']
    # 技术 regime (用 GEM)
    tech_regime = detect_regime(gem['close'], gem['ma200'], gem['slope'],
                                macro['regime'] if macro else 'neutral')
    # 风格相对强弱
    mom = {
        '创业板50': gem['ret60'],
        '沪深300': hs300['ret60'],
        '上证50': f50['ret60'],
        '红利': div['ret60'],
    }
    mom_sorted = sorted([(k, v) for k, v in mom.items() if v is not None],
                        key=lambda x: x[1], reverse=True)
    leader = mom_sorted[0] if mom_sorted else ('创业板50', None)

    reg_cn = {'bull': '牛市', 'sideways': '震荡市', 'bear': '熊市'}.get(tech_regime, '未知')
    slope_cn = '上行' if (gem['slope'] or 0) > 0 else '下行'
    above_cn = '站上' if gem['above_ma200'] else '跌破'

    # 行情趋势判断
    trend = (
        f"截至 {as_of}，策略框架判定的市场状态为【{reg_cn}】。"
        f"创业板50ETF 最新收盘 {gem['close']}，{above_cn}其 MA200({gem['ma200']})，"
        f"MA200 斜率 {gem['slope']}%（{slope_cn}）；近20日/60日涨跌分别为 "
        f"{gem['ret20']}% / {gem['ret60']}%，年化波动率约 {gem['ann_vol']}%。\n"
        f"风格上，近60日相对强弱领先的是【{leader[0]}】"
        f"(+{leader[1]}%)；沪深300 {hs300['ret60']}%、上证50 {f50['ret60']}%、红利 {div['ret60']}%。"
    )
    if macro and macro['available']:
        trend += (f"\n宏观面: 5指标评分 {macro['score']}（{ {'bull':'偏多','bear':'偏空','neutral':'中性'}[macro['regime']] }），"
                  f"PMI {macro['detail'].get('pmi')}、CPI {macro['detail'].get('cpi')}、"
                  f"PPI {macro['detail'].get('ppi')}、M2 {macro['detail'].get('m2')}。")
    else:
        trend += "\n宏观面: 本地宏观数据缺失/过期，本次未融合宏观评分（仅技术面判断）。"

    # 策略观点 (基于 +① 防守超时框架)
    if tech_regime == 'bull':
        action = "持有 / 逢调仓维持 创业板50ETF(成长主线)"
        reason = (f"价格站上 MA200 且斜率未确认下行，技术牛市延续；"
                  f"GEM 60日动量 {gem['ret60']}% 提供成长弹性。维持满仓成长，"
                  f"仅在出现 MA200 斜率转负且价格跌破 MA200 时切换防守。")
        key_signals = [
            "MA200 斜率由正转负（牛→震荡/熊的先行信号）",
            "创业板50 收盘价有效跌破 MA200",
            "宏观评分转负（PMI<49 / M2<8）触发降级",
        ]
    elif tech_regime == 'sideways':
        action = "防守 / 轻仓，现金或低波动避风港为主"
        reason = (f"价格位于 MA200 下方、斜率未确认下行，属震荡市；框架默认现金（不追跌）。"
                  f"若 GEM 60日动量恢复至 >{MOMENTUM_THRESHOLD}%（当前 {gem['ret60']}%）"
                  f"且防守已持续 >{MAX_DEFENSE_DAYS} 天，按 +① 防守超时 回切创业板50。")
        key_signals = [
            "GEM 重新站上 MA200 并伴随斜率转正 → 回补成长",
            f"GEM 60日动量突破 {MOMENTUM_THRESHOLD}% 阈值",
            "宏观评分转多（PMI>51 / M2>11）支撑升级为牛",
        ]
    else:  # bear
        action = "空仓 / 货币基金；如需持仓选 上证50ETF 作低波动避风港"
        reason = (f"价格跌破 MA200 且 MA200 斜率下行，技术熊市；框架清仓成长。"
                  f"红利 ETF 历史回测显示不具防御性，已不纳入；"
                  f"仅保留上证50ETF 作为极低波动的过渡选项或完全空仓。")
        key_signals = [
            "MA200 斜率重新转正 + 价格收复 MA200 → 熊→震荡/牛",
            "政策/流动性拐点（M2 回升、SHIBOR 下行）",
            "GEM 跌幅收敛、波动率高位回落",
        ]

    risk_notes = [
        f"本框架在 2015-2016 创业板极端崩盘中最大回撤约 64.7%，极端成长股下行无有效保护；当前波动率 {gem['ann_vol']}% 偏高时需警惕。",
        "日线级信号存在 1 日执行滞后与滑点，震荡市 whipsaw 会损耗收益。",
        "宏观评分为本地 CSV 月度数据前向填充，存在约 1 个月滞后，非实时。",
        "以上为回测框架的模型信号，非个人投资建议；实际决策需结合自身风险承受能力。",
    ]

    return {
        'as_of': as_of,
        'fused_regime': tech_regime,
        'regime_cn': reg_cn,
        'recommended_action': action,
        'recommendation_reason': reason,
        'leader_style': leader[0],
        'trend_read': trend,
        'strategy_view': (
            f"【本月主基调】{reg_cn} · {action}\n"
            f"【持仓建议】{reason}\n"
            f"【关键观察信号】" + "；".join(key_signals) + "\n"
            f"【风格轮动】近60日领先 {leader[0]}，可作为牛市加仓的优先方向；"
            f"震荡/熊市则降低该类高 Beta 敞口。"
        ),
        'key_signals': key_signals,
        'risk_notes': risk_notes,
    }


def main():
    print("=" * 60)
    print("ETF 牛熊择时 · 月度观点生成")
    print("=" * 60)
    quotes = {}
    # GEM 主线: 159949 优先, 失败用 159915 代理 (趋势一致)
    gem_code = "159949.SZ"
    try:
        gdf = westock_kline("sz159949", limit=320)
        gem_src = "159949.SZ"
    except Exception as e:
        print(f"  GEM 159949 失败, 改用 159915 代理: {e}")
        gdf = westock_kline("sz159915", limit=320)
        gem_src = "159915.SZ (代理)"
    quotes[gem_code] = indicators(gdf)
    print(f"  GEM主线 ({gem_src}): {quotes[gem_code]['date']} 收 {quotes[gem_code]['close']} "
          f"MA200 {quotes[gem_code]['ma200']} 斜率 {quotes[gem_code]['slope']}% "
          f"60日 {quotes[gem_code]['ret60']}%")

    # 其余 ETF: 尽力拉取, 失败则跳过该风格
    for code, name in ETFS.items():
        if code == gem_code:
            continue
        pref = "sh" + code.split('.')[0] if code.endswith(".SH") else "sz" + code.split('.')[0]
        try:
            df = westock_kline(pref, limit=320)
            quotes[code] = indicators(df)
            print(f"  {name} ({code}): {quotes[code]['date']} 收 {quotes[code]['close']} "
                  f"MA200 {quotes[code]['ma200']} 斜率 {quotes[code]['slope']}% "
                  f"60日 {quotes[code]['ret60']}%")
        except Exception as e:
            print(f"  {name} ({code}) 拉取失败, 跳过: {e}")
            quotes[code] = None

    macro = None
    m = load_macro()
    if m is not None:
        try:
            score, regime, detail = macro_score(m)
            macro = {'available': True, 'score': score, 'regime': regime, 'detail': detail}
            print(f"  宏观评分: {score} ({regime})")
        except Exception as e:
            print(f"  宏观评分失败: {e}")
    if macro is None:
        macro = {'available': False, 'score': None, 'regime': 'neutral', 'detail': {}}

    view = build_view(quotes['159949.SZ'], quotes['510300.SH'],
                      quotes['510050.SH'], quotes['510880.SH'], macro)

    out = {
        'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'as_of': view['as_of'],
        'etfs': quotes,
        'macro': {'available': macro['available'], 'score': macro['score'],
                  'regime': macro['regime'], 'detail': macro['detail']},
        'fused_regime': view['fused_regime'],
        'regime_cn': view['regime_cn'],
        'recommended_action': view['recommended_action'],
        'recommendation_reason': view['recommendation_reason'],
        'leader_style': view['leader_style'],
        'trend_read': view['trend_read'],
        'strategy_view': view['strategy_view'],
        'key_signals': view['key_signals'],
        'risk_notes': view['risk_notes'],
    }
    out_path = OUTPUT_DIR / 'monthly_review.json'
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print("\n" + "=" * 60)
    print("【行情趋势判断】")
    print(view['trend_read'])
    print("\n【本月投资策略】")
    print(view['strategy_view'])
    print("\n【风险点】")
    for r in view['risk_notes']:
        print(f"  - {r}")
    print("=" * 60)
    print(f"已落盘: {out_path} (as_of={view['as_of']}, regime={view['regime_cn']})")


if __name__ == "__main__":
    main()
