#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从 5 档 summary.json 生成 README.md（GitHub Actions CI 用）。

用法: python make_readme.py --output-dir <报告目录>
"""
import os
import sys
import json
import glob
from pathlib import Path


def main():
    od = None
    for i, a in enumerate(sys.argv[1:], 1):
        if a == "--output-dir" and i < len(sys.argv):
            od = sys.argv[i + 1]
    if not od:
        od = os.path.expanduser("~/Desktop")
    od = Path(od)

    rows = []
    for p in sorted(glob.glob(str(od / "gem50_bullbear_v10_*_summary.json"))):
        try:
            d = json.load(open(p, encoding="utf-8"))
        except Exception:
            continue
        name = d.get("meta", {}).get("strategy_name", Path(p).name)
        s = d.get("summary", {})
        rows.append((name, s.get("total_return_pct"), s.get("annual_return_pct"),
                     s.get("max_drawdown_pct"), s.get("sharpe"), s.get("win_rate_pct"),
                     s.get("total_trades")))

    if not rows:
        print("[make_readme] 未找到 summary.json，跳过")
        return

    md = ["# ETF 牛熊择时 · 周度分析", "",
          "> 由 GitHub Actions 自动生成（云端运行，脱离本机）。",
          "", "## 📊 五档回测总览", "",
          "| 等级 | 总收益% | 年化% | 最大回撤% | Sharpe | 胜率% | 交易 |",
          "|------|---------|-------|-----------|--------|-------|------|"]
    for r in rows:
        md.append("| %s | %.1f | %.1f | %.1f | %.2f | %.1f | %d |" % r)
    md += ["", "## 📁 文件索引", "",
           "- 仪表盘：`index.html`（下载后双击打开）",
           "- 回测数据：`gem50_bullbear_v10_*_*.csv`",
           "- 月度研判：`monthly_review.json`",
           "- 参数快照：`gem50_bullbear_v10_*_summary.json`（含 params / daily_snapshot）",
           "",
           "> ⚠️ 以上内容由 AI 基于公开信息整理生成，仅供参考，不构成任何投资建议或个股推荐。投资有风险，决策需谨慎。"]

    out = od / "README.md"
    out.write_text("\n".join(md), encoding="utf-8")
    print(f"[make_readme] 已写入 {out}（{len(rows)} 档）")


if __name__ == "__main__":
    main()
