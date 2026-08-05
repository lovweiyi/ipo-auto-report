#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""云端行情刷新：用 akshare 拉取 5 只 ETF 全历史日线（前复权），覆盖写入本地缓存。

GitHub Actions 上 akshare 可直连东方财富接口（与仓库 IPO 工作流同源）。
- 输出列对齐本地缓存格式：date,open,close,high,low
- best-effort：单只失败不影响其他；全部失败则保留既有静态缓存并正常退出（不让整条 run 崩）。

用法: python refresh_cloud.py
依赖环境变量 ETF_HISTORY_DIR（指向 etf_history 目录）。
"""
import os
import sys
import json
import datetime as dt

# ETF 代码映射：本地缓存文件名 -> akshare 6位代码
ETF_MAP = {
    "sh510050": "510050",  # 上证50ETF
    "sh510300": "510300",  # 沪深300ETF
    "sh510880": "510880",  # 红利ETF
    "sz159915": "159915",  # 创业板ETF
    "sz159949": "159949",  # 创业板50ETF
}


def main():
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
    start = "20100101"
    ok, fail = 0, 0
    for fname, code in ETF_MAP.items():
        try:
            df = ak.fund_etf_hist_em(symbol=code, period="daily",
                                     start_date=start, end_date=end, adjust="qfq")
            if df is None or len(df) == 0:
                raise RuntimeError("空数据")
            # 列映射：日期/开盘/收盘/最高/最低
            out = df.rename(columns={
                "日期": "date", "开盘": "open", "收盘": "close",
                "最高": "high", "最低": "low",
            })[["date", "open", "close", "high", "low"]].copy()
            out["date"] = out["date"].astype(str)
            for c in ("open", "close", "high", "low"):
                out[c] = out[c].astype(float).round(4)
            out = out.sort_values("date").reset_index(drop=True)
            path = os.path.join(etf_dir, f"{fname}.csv")
            out.to_csv(path, index=False)
            print(f"[refresh] {fname} ({code}) 写入 {len(out)} 行, 末日={out['date'].iloc[-1]}")
            ok += 1
        except Exception as e:
            print(f"[refresh] {fname} ({code}) 失败: {type(e).__name__}: {e}")
            fail += 1

    print(f"[refresh] 完成：成功 {ok} / 失败 {fail}")
    if ok == 0:
        print("[refresh] 全部失败，保留既有静态缓存（best-effort）")
    # 即使部分失败也正常退出，不让 workflow 中断


if __name__ == "__main__":
    main()
