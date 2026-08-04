#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
send_email.py — 通过 QQ 邮箱 SMTP 发送 ipo_report.html 附件

依赖环境变量（由 GitHub Actions Secrets 注入，不在代码里写明文）：
  QQ_EMAIL      发件人 QQ 邮箱，例如 790192539@qq.com
  QQ_AUTH_CODE  QQ 邮箱“授权码”（不是登录密码）
  RECIPIENT     收件人（可选，默认 = QQ_EMAIL，即发给自己）

本地手动测试示例：
  QQ_EMAIL=790192539@qq.com QQ_AUTH_CODE=xxxx python send_email.py
"""
import os
import re
import smtplib
import datetime as dt
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication

REPORT = "ipo_report.html"
SMTP_HOST, SMTP_PORT = "smtp.qq.com", 587


def build_subject():
    """从报告 HTML 中提取胜率/有效样本，拼进邮件主题"""
    today = dt.date.today().strftime("%Y-%m-%d")
    suffix = ""
    try:
        html = open(REPORT, encoding="utf-8").read()
        m = re.search(r"胜率\s*([\d.]+)%", html)
        v = re.search(r"有效\s*(\d+)\s*支", html)
        if m:
            suffix += f" 胜率{m.group(1)}%"
        if v:
            suffix += f" 有效{v.group(1)}支"
    except Exception:
        pass
    return f"新股自动分析报告（{today}）{suffix}"


def main():
    sender = os.environ.get("QQ_EMAIL")
    auth = os.environ.get("QQ_AUTH_CODE")
    recipient = (os.environ.get("RECIPIENT") or sender)

    if not (sender and auth):
        print("[skip] 缺少 QQ_EMAIL / QQ_AUTH_CODE，跳过发送")
        return
    if not os.path.exists(REPORT):
        print(f"[skip] {REPORT} 不存在，跳过发送")
        return

    subject = build_subject()
    today = dt.date.today().strftime("%Y-%m-%d")

    msg = MIMEMultipart()
    msg["From"] = sender
    msg["To"] = recipient
    msg["Subject"] = subject
    body = (f"附件为 {today} 自动生成的新股分析报告：\n"
            f"· 模块A：近端 10 支新股「首日收盘买入、次日卖出」回测\n"
            f"· 模块B：本次上市新股画像（市值/行业市值/盈利增速/发行流通市值）\n\n"
            f"本报告由 GitHub Actions 自动生成，仅供研究参考，不构成投资建议。")
    msg.attach(MIMEText(body, "plain", "utf-8"))

    with open(REPORT, "rb") as f:
        part = MIMEApplication(f.read(), Name=REPORT)
    part["Content-Disposition"] = f'attachment; filename="{REPORT}"'
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
