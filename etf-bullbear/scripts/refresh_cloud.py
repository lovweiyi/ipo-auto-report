#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""云端行情刷新：多源拉取 5 只 ETF 全历史日线，覆盖写入本地缓存。

数据源（按优先级）：
  1) 新浪 fund_etf_hist_sina  —— 境外 runner 上通常比东方财富更不易被限流
  2) 东方财富 fund_etf_hist_em(adjust=qfq) —— 兜底，与原始缓存语义一致

容错设计：
  - 每只 ETF 先试主源、失败切兜底源
  - 整批未全成功时，冷却 COOLDOWN 秒后重跑剩余标的（最多 BATCH_RETRY 轮）
  - best-effort：最终仍有失败则保留既有静态缓存，不让整条 workflow 中断

输出列对齐本地缓存格式：date,open,close,high,low
用法: python refresh_cloud.py  （依赖环境变量 ETF_HISTORY_DIR）
"""
import os
import sys
import time
import datetime as dt

# 本地缓存文件名 -> akshare 6位代码
ETF_MAP = {
    "sh510050": "510050",  # 上证50ETF
    "sh510300": "510300",  # 沪深300ETF
    "sh510880": "510880",  # 红利ETF
    "sz159915": "159915",  # 创业板ETF
    "sz159949": "159949",  # 创业板50ETF
}

BATCH_RETRY = 3     # 整批重试轮数
COOLDOWN = 45       # 批次间冷却（秒）
BETWEEN = 2         # 标的之间间隔（秒）


def _norm(df, source):
    """统一成 date,open,close,high,low（顺序与本地缓存一致）。"""
    if source == "sina":
        cols = {"date": "date", "open": "open", "close": "close",
                "high": "high", "low": "low"}
    else:  # eastmoney
        cols = {"日期": "date", "开盘": "open", "收盘": "close",
                "最高": "high", "最低": "low"}
    out = df.rename(columns=cols)[list(cols.values())].copy()
    out["date"] = pd.to_datetime(out["date"]).dt.strftime("%Y-%m-%d")
    for c in ("open", "close", "high", "low"):
        out[c] = out[c].astype(float).round(4)
    return out.sort_values("date").reset_index(drop=True)


def fetch_one(ak, code, end):
    """主源新浪 -> 兜底东财 qfq；全部失败抛异常。"""
    # 1) 新浪
    try:
        df = ak.fund_etf_hist_sina(symbol=code)
        if df is not None and len(df) > 0:
            return _norm(df, "sina"), "sina"
    except Exception as e:
        pass
    # 2) 东方财富 qfq
    df = ak.fund_etf_hist_em(symbol=code, period="daily",
                             start_date="20100101", end_date=end, adjust="qfq")
    if df is None or len(df) == 0:
        raise RuntimeError("空数据")
    return _norm(df, "em"), "em"


def main():
    global pd
    import pandas as pd
    etf_dir = os.environ.get("ETF_HISTORY_DIR")
    if not etf_dir:
        print("[refresh] 未设置 ETF_HISTORY_DIR，跳过")
        return
    os.makedirs(etf_dir, exist_ok=True)

    try:
        import akshare as ak
    except Exception as e:
        print(f"[refresh] akshare 不可用（{e}），保留静态缓存")
        return

    end = dt.date.today().strftime("%Y%m%d")
    remaining = list(ETF_MAP.items())
    ok_total = 0
    for batch in range(BATCH_RETRY):
        failed = []
        for fname, code in remaining:
            try:
                out, src = fetch_one(ak, code, end)
                path = os.path.join(etf_dir, f"{fname}.csv")
                out.to_csv(path, index=False)
                print(f"[refresh] {fname} ({code}) [{src}] 写入 {len(out)} 行, 末日={out['date'].iloc[-1]}")
                ok_total += 1
            except Exception as e:
                print(f"[refresh] {fname} ({code}) 失败: {type(e).__name__}: {e}")
                failed.append((fname, code))
            time.sleep(BETWEEN)
        remaining = failed
        if not remaining:
            break
        if batch < BATCH_RETRY - 1:
            print(f"[refresh] 第{batch+1}轮剩余 {len(remaining)} 只，冷却 {COOLDOWN}s 后重试")
            time.sleep(COOLDOWN)

    fail = len(remaining)
    print(f"[refresh] 完成：成功 {ok_total} / 失败 {fail}")
    if fail:
        print(f"[refresh] 未刷新: {[f for f,_ in remaining]}（保留既有静态缓存，best-effort）")


if __name__ == "__main__":
    main()
