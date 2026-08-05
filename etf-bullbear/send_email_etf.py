#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
send_email_etf.py — 通过 QQ 邮箱 SMTP 发送 ETF 周报（index.html + README.md 附件）。

凭据来源（GitHub Actions 由 Secrets 注入；本地可 export 同名环境变量）:
  QQ_EMAIL    发件人 QQ 邮箱
  QQ_AUTH_CODE QQ 邮箱授权码（16 位，不是登录密码）
  RECIPIENT   收件人（省略则发给自己）

用法: python send_email_etf.py --folder <报告目录>
"""
import os
import sys
import smtplib
import datetime as dt
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication

SMTP_HOST, SMTP_PORT = "smtp.qq.com", 587


def main():
    folder = None
    for i, a in enumerate(sys.argv[1:], 1):
        if a == "--folder" and i < len(sys.argv):
            folder = sys.argv[i + 1]
    if not folder:
        print("[skip] 缺少 --folder 参数")
        return

    sender = os.environ.get("QQ_EMAIL")
    auth = os.environ.get("QQ_AUTH_CODE")
    recipient = (os.environ.get("RECIPIENT") or sender)

    if not (sender and auth):
        print("[skip] 缺少 QQ_EMAIL / QQ_AUTH_CODE，跳过发送")
        return

    idx = os.path.join(folder, "index.html")
    if not os.path.exists(idx):
        print(f"[skip] {idx} 不存在，跳过发送")
        return

    today = dt.date.today().strftime("%Y-%m-%d")
    subject = f"ETF牛熊择时周报 {today}"

    msg = MIMEMultipart()
    msg["From"] = sender
    msg["To"] = recipient
    msg["Subject"] = subject
    body = (
        f"附件为 {today} 自动生成的 ETF 牛熊择时周报（GitHub Actions 云端运行）。\n"
        f"· 仪表盘 index.html（含五档回测对比 + 各档明细）\n"
        f"· README.md（五档指标表 + 文件索引）\n\n"
        f"本报告由自动流程生成，仅供研究参考，不构成投资建议。"
    )
    msg.attach(MIMEText(body, "plain", "utf-8"))

    for fn in ("index.html", "README.md"):
        fp = os.path.join(folder, fn)
        if os.path.exists(fp):
            with open(fp, "rb") as f:
                part = MIMEApplication(f.read(), Name=fn)
            part["Content-Disposition"] = f'attachment; filename="{fn}"'
            msg.attach(part)

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as s:
            s.starttls()
            s.login(sender, auth)
            s.sendmail(sender, [recipient], msg.as_string())
        print(f"[ok] 已发送邮件至 {recipient}（主题：{subject}）")
    except Exception as e:
        print(f"[error] 发送失败：{type(e).__name__}: {e}")
        raise


if __name__ == "__main__":
    main()
