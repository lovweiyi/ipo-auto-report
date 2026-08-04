#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ipo_auto_report.py — 上市新股自动分析报告流程
=============================================

两大模块（对应需求）：
  【模块A】近端 N 支新股 "首日收盘价买入、次日卖出" 盈亏回测
  【模块B】本次上市新股画像：市值 / 行业市值 / 盈利增速 / 发行流通市值

数据源：akshare（需联网）。本脚本可在本机每日定时运行，自动产出 HTML 报告。
对偶发被限流的接口做了 retry + 优雅降级（不编造、缺数标注 N/A）。

用法：
  python ipo_auto_report.py            # 默认 近端10支 + 本次新股画像
  python ipo_auto_report.py --n 10 --top 25 --out ipo_report.html
"""

import argparse
import datetime as dt
import json
import time
import numpy as np
import pandas as pd
import akshare as ak

# ----------------------------------------------------------------------------
# 通用工具
# ----------------------------------------------------------------------------
def retry(fn, n=3, wait=2):
    last = None
    for i in range(n):
        try:
            return fn()
        except Exception as e:  # noqa
            last = e
            if i < n - 1:
                time.sleep(wait)
    raise last


def today_str():
    return dt.date.today().strftime("%Y%m%d")


# ----------------------------------------------------------------------------
# 模块A：近端新股 "首日买 / 次日卖" 回测
# ----------------------------------------------------------------------------
def get_ipo_universe(top=20, cache="ipo_universe.csv"):
    """东财近端新股列表（含代码、名称、最新价、涨跌幅等）。
    若东财接口被限流，则回退到本地缓存 ipo_universe.csv（由 MCP/data_ipo 预置）。"""
    try:
        df = retry(lambda: ak.stock_zh_a_new_em())
        df.to_csv(cache, index=False, encoding="utf-8-sig")
        return df.head(top).reset_index(drop=True)
    except Exception as e:  # noqa
        print(f"  [warn] 东财新股接口受限({type(e).__name__})，回退本地缓存 {cache}")
        try:
            df = pd.read_csv(cache, dtype={"代码": str})
            return df.head(top).reset_index(drop=True)
        except Exception as e2:  # noqa
            raise RuntimeError(f"无法获取新股清单且无缓存: {e2}")


def fetch_kline(symbol, start="19900101", end=None):
    end = end or today_str()
    return retry(lambda: ak.stock_zh_a_hist(
        symbol=symbol, period="daily", start_date=start,
        end_date=end, adjust=""))


def backtest_last_n(n=10, top=20):
    uni = get_ipo_universe(top=top)
    rows = []
    for _, r in uni.iterrows():
        code = str(r["代码"]).strip()
        name = str(r["名称"]).strip()
        rec = {"code": code, "name": name, "status": "OK"}
        try:
            k = fetch_kline(code)
            if k is None or len(k) < 2:
                rec.update(status="K线不足(<2日)", listing=None,
                           issue=None, day1=None, day2=None, ret=None,
                           first_day_chg=None)
                rows.append(rec)
                continue
            k = k.reset_index(drop=True)
            rec["listing"] = str(k.iloc[0]["日期"])
            rec["issue"] = float(k.iloc[0]["开盘"])      # 发行价≈首日开盘
            rec["day1"] = float(k.iloc[0]["收盘"])        # 首日收盘
            rec["day2"] = float(k.iloc[1]["收盘"])        # 次日收盘
            rec["first_day_chg"] = (rec["day1"] - rec["issue"]) / rec["issue"] * 100
            rec["ret"] = (rec["day2"] - rec["day1"]) / rec["day1"] * 100  # 首日买次日卖
        except Exception as e:  # noqa
            rec.update(status=f"ERR:{type(e).__name__}", listing=None,
                       issue=None, day1=None, day2=None, ret=None,
                       first_day_chg=None)
        rows.append(rec)

    res = pd.DataFrame(rows)
    res["listing_dt"] = pd.to_datetime(res["listing"], errors="coerce")
    res = res.sort_values("listing_dt", ascending=False).reset_index(drop=True)
    last = res.head(n).copy()

    ok = last[last["status"] == "OK"]
    if len(ok):
        cum = float(np.prod(1 + ok["ret"].astype(float) / 100) - 1) * 100
        stats = {
            "count": int(len(last)),
            "valid": int(len(ok)),
            "win": int((ok["ret"].astype(float) > 0).sum()),
            "win_rate": round((ok["ret"].astype(float) > 0).mean() * 100, 1),
            "avg_ret": round(ok["ret"].astype(float).mean(), 2),
            "med_ret": round(ok["ret"].astype(float).median(), 2),
            "best": round(ok["ret"].astype(float).max(), 2),
            "worst": round(ok["ret"].astype(float).min(), 2),
            "cum_ret": round(cum, 2),
        }
    else:
        stats = {"count": int(len(last)), "valid": 0}
    return last, stats


# ----------------------------------------------------------------------------
# 模块B：本次上市新股画像
# ----------------------------------------------------------------------------
def earnings_growth(symbol):
    """从利润摘要取 归母净利润 的同比增速（年报 + 最新季报）"""
    try:
        fa = retry(lambda: ak.stock_financial_abstract(symbol=symbol))
    except Exception:
        return None
    row = fa[fa["指标"] == "归母净利润"]
    if row.empty:
        return None
    date_cols = [c for c in fa.columns if str(c).isdigit()]
    ser = {}
    for c in date_cols:
        v = row[c].values[0]
        try:
            ser[c] = float(v)
        except Exception:
            ser[c] = np.nan
    out = {}
    # 年报同比
    for y in ("20231231", "20241231", "20251231"):
        if y in ser and (str(int(y) - 10000) in ser) and not np.isnan(ser[str(int(y)-10000)]) and ser[str(int(y)-10000)] != 0:
            out[y] = round((ser[y] - ser[str(int(y)-10000)]) / abs(ser[str(int(y)-10000)]) * 100, 1)
    # 最新季报同比（取最大日期列）
    qcols = sorted([c for c in date_cols if c.endswith(("0331", "0630", "0930")) and c not in out], reverse=True)
    if qcols:
        q = qcols[0]
        # 找去年同季
        ly = str(int(q[:4]) - 1) + q[4:]
        if ly in ser and not np.isnan(ser[ly]) and ser[ly] != 0:
            out["最新季(" + q + ")"] = round((ser[q] - ser[ly]) / abs(ser[ly]) * 100, 1)
    return out


def profile_latest_ipo(n=10, top=20):
    """对最近一支上市新股做画像（市值/行业市值/盈利增速/发行流通市值）"""
    last, _ = backtest_last_n(n=n, top=top)
    ok = last[last["status"] == "OK"]
    if ok.empty:
        return None, None
    code = str(ok.iloc[0]["code"])
    name = str(ok.iloc[0]["name"])
    issue = ok.iloc[0]["issue"]
    listing = ok.iloc[0]["listing"]

    prof = {
        "code": code, "name": name, "listing": listing, "issue_price": issue,
        "mkt_cap": None, "float_cap": None, "industry": None,
        "industry_cap": None, "issue_float_cap": None, "earn_growth": None,
        "notes": [],
    }

    # 盈利增速（稳定可用）
    try:
        prof["earn_growth"] = earnings_growth(code)
    except Exception as e:  # noqa
        prof["notes"].append(f"盈利增速获取失败: {e}")

    # 市值 / 流通市值 / 行业（东财大表，可能被限流 → 降级）
    try:
        spot = retry(lambda: ak.stock_zh_a_spot_em())
        s = spot[spot["代码"] == code]
        if not s.empty:
            prof["mkt_cap"] = float(s.iloc[0]["总市值"])
            prof["float_cap"] = float(s.iloc[0]["流通市值"])
            prof["industry"] = str(s.iloc[0].get("行业", "")) if "行业" in s.columns else None
    except Exception as e:  # noqa
        prof["notes"].append(f"实时市值接口受限: {type(e).__name__}")

    # 行业市值（板块总市值）
    try:
        ind = retry(lambda: ak.stock_board_industry_name_em())
        if prof["industry"] and prof["industry"] in set(ind["板块名称"].astype(str)):
            cons = retry(lambda: ak.stock_board_industry_cons_em(symbol=prof["industry"]))
            # 用成分股实时市值求和
            spot2 = retry(lambda: ak.stock_zh_a_spot_em())
            m = spot2[spot2["代码"].isin(cons["代码"].astype(str))]
            prof["industry_cap"] = float(m["总市值"].sum())
    except Exception as e:  # noqa
        prof["notes"].append(f"行业市值接口受限: {type(e).__name__}")

    # 发行流通市值 = 发行价 × 首发流通股本（首发流通股本≈上市初期流通股本）
    try:
        if prof["float_cap"] and issue:
            # float_cap 为最新流通市值；首发流通股本≈ float_cap/最新价，
            # 粗略用 (float_cap/最新价)*发行价 估算发行流通市值
            spot3 = retry(lambda: ak.stock_zh_a_spot_em())
            s3 = spot3[spot3["代码"] == code]
            if not s3.empty:
                cur_price = float(s3.iloc[0]["最新价"])
                ipo_float_shares = prof["float_cap"] / cur_price  # 近似首发流通股
                prof["issue_float_cap"] = issue * ipo_float_shares
    except Exception:
        prof["notes"].append("发行流通市值估算受限")

    return prof, last


# ----------------------------------------------------------------------------
# HTML 渲染
# ----------------------------------------------------------------------------
def fmt(x, unit="", nd=2):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "N/A"
    if isinstance(x, float):
        return f"{x:,.{nd}f}{unit}"
    return f"{x}{unit}"


def render_html(last, stats, prof, out_path):
    gen = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    # 模块A 表格
    a_rows = ""
    for _, r in last.iterrows():
        ret = r.get("ret")
        color = ""
        if ret is not None and not (isinstance(ret, float) and np.isnan(ret)):
            color = "style='color:#c0392b'" if ret < 0 else "style='color:#1e8449'"
        a_rows += (
            f"<tr><td>{r['code']}</td><td>{r['name']}</td>"
            f"<td>{fmt(r.get('listing'))}</td>"
            f"<td class='num'>{fmt(r.get('issue'))}</td>"
            f"<td class='num'>{fmt(r.get('day1'))}</td>"
            f"<td class='num'>{fmt(r.get('day2'))}</td>"
            f"<td class='num' {color}>{fmt(ret, '%')}</td>"
            f"<td>{r.get('status')}</td></tr>"
        )
    st = stats
    stat_html = (
        f"样本 {st.get('count','-')} 支（有效 {st.get('valid','-')} 支）｜"
        f"胜率 {st.get('win_rate','-')}%｜平均 {fmt(st.get('avg_ret'),'%')}｜"
        f"中位数 {fmt(st.get('med_ret'),'%')}｜最佳 {fmt(st.get('best'),'%')}｜"
        f"最差 {fmt(st.get('worst'),'%')}｜累计 {fmt(st.get('cum_ret'),'%')}"
    ) if st.get("valid") else "本环境无有效样本"

    # 模块B
    if prof:
        eg = prof.get("earn_growth") or {}
        eg_html = "；".join(f"{k}: {v}%" for k, v in eg.items()) or "N/A"
        b_html = f"""
        <h3>本次上市新股：{prof['name']}（{prof['code']}）上市日 {prof['listing']}</h3>
        <table class='kv'>
          <tr><th>发行价</th><td>{fmt(prof.get('issue_price'))}</td>
              <th>总市值(实时)</th><td>{fmt(prof.get('mkt_cap'),'元')}</td></tr>
          <tr><th>流通市值(实时)</th><td>{fmt(prof.get('float_cap'),'元')}</td>
              <th>所属行业</th><td>{prof.get('industry') or 'N/A'}</td></tr>
          <tr><th>行业市值</th><td>{fmt(prof.get('industry_cap'),'元')}</td>
              <th>发行流通市值(估算)</th><td>{fmt(prof.get('issue_float_cap'),'元')}</td></tr>
          <tr><th>盈利增速(归母净利同比)</th><td colspan='3'>{eg_html}</td></tr>
        </table>
        <p class='note'>说明：实时市值/行业市值来自东财大表接口，部分环境可能被限流；
        盈利增速来自利润摘要（稳定）。发行流通市值为 发行价×首发流通股本 的估算值。</p>
        """
    else:
        b_html = "<p>本次上市新股画像：无有效样本。</p>"

    html = f"""<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'>
<title>上市新股自动分析报告</title>
<style>
 body{{font-family:-apple-system,'Segoe UI','Microsoft YaHei',sans-serif;margin:24px;color:#222;}}
 h1{{border-bottom:3px solid #2c3e50;padding-bottom:8px;}}
 h3{{margin-top:28px;color:#2c3e50;}}
 table{{border-collapse:collapse;width:100%;margin:10px 0;font-size:13px;}}
 th,td{{border:1px solid #ddd;padding:6px 8px;text-align:left;}}
 th{{background:#f4f6f7;}}
 .num{{text-align:right;font-variant-numeric:tabular-nums;}}
 .kv th{{width:140px;background:#f4f6f7;}}
 .stat{{background:#eaf2f8;padding:10px;border-radius:6px;margin:10px 0;font-weight:600;}}
 .note{{color:#7f8c8d;font-size:12px;}}
 .meta{{color:#95a5a6;font-size:12px;}}
</style></head><body>
<h1>上市新股自动分析报告</h1>
<p class='meta'>生成时间：{gen}　数据源：akshare（东财/利润摘要）　流程：ipo_auto_report.py</p>

<h2>模块A　近端新股「首日收盘买入 · 次日卖出」回测</h2>
<div class='stat'>{stat_html}</div>
<table><thead><tr><th>代码</th><th>名称</th><th>上市日</th><th>发行价</th>
<th>首日收盘</th><th>次日收盘</th><th>策略收益</th><th>状态</th></tr></thead>
<tbody>{a_rows}</tbody></table>
<p class='note'>策略：上市首日收盘价买入，次日收盘价卖出（不考虑交易成本）。
样本为东财近端新股列表按上市日倒序取前 N 支。</p>

<h2>模块B　本次上市新股画像</h2>
{b_html}

<p class='note'>本报告由自动流程生成，仅供研究参考，不构成个人投资建议。</p>
</body></html>"""
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    return out_path


# ----------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=10)
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--out", default="ipo_report.html")
    args = ap.parse_args()

    print(f"[1/3] 回测近端 {args.n} 支新股 ...")
    last, stats = backtest_last_n(n=args.n, top=args.top)
    print("   统计:", json.dumps(stats, ensure_ascii=False))

    print("[2/3] 本次上市新股画像 ...")
    prof, _ = profile_latest_ipo(n=args.n, top=args.top)
    if prof:
        print("   标的:", prof["name"], prof["code"], "| 盈利增速:", prof["earn_growth"])

    print(f"[3/3] 渲染 HTML -> {args.out}")
    render_html(last, stats, prof, args.out)
    print("完成。")


if __name__ == "__main__":
    main()
