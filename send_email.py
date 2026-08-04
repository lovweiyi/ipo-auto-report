#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
send_email.py — 通过 QQ 邮箱 SMTP 发送 ipo_report.html 附件

凭据来源（按优先级）：
  1. 环境变量 QQ_EMAIL / QQ_AUTH_CODE / RECIPIENT
     （GitHub Actions 由 Secrets 注入；本地可在 shell 直接 export）
  2. 本地 .env 文件（项目根目录，已被 .gitignore 排除，不会上传）
  注意：授权码不是登录密码，是 QQ 邮箱“设置→账户→开启 SMTP”后生成的 16 位码。
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


def load_env_file(path=".env"):
    """本地开发时从 .env 读取凭据；setdefault 不覆盖已有环境变量（兼容 GitHub Secrets）。"""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


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
    load_env_file()
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
            f"本报告由自动流程生成，仅供研究参考，不构成投资建议。")
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
