# 新股自动分析 · GitHub Actions 云端部署指南

目标：把 `ipo_auto_report.py` 托管到 GitHub，**每周日云端自动运行**并**通过 QQ 邮箱发送报告**，
彻底脱离本机——电脑关机也能跑。

## 已就绪的文件
| 文件 | 作用 |
|---|---|
| `ipo_auto_report.py` | 分析主脚本（回测 + 新股画像 + 输出 HTML） |
| `.github/workflows/ipo_report.yml` | 定时触发器：每周日 UTC 01:00（北京周日 09:00）跑脚本 + 发邮件 |
| `send_email.py` | 用 QQ 邮箱 SMTP 把 `ipo_report.html` 作为附件发出 |
| `ipo_universe.csv` | 东财近端新股清单缓存（接口被限流时回退；workflow 会自动刷新回仓库） |

## 你需要做的 3 步

### 1. 把本目录推送到 GitHub 仓库
```bash
cd C:\Users\loveweiyi\WorkBuddy\2026-08-04-08-05-15
git init
git add .github ipo_auto_report.py send_email.py ipo_universe.csv requirements.txt
git commit -m "feat: 新股自动分析 GitHub Actions 方案"
git remote add origin https://github.com/<你的用户名>/<仓库名>.git
git branch -M main
git push -u origin main
```
> 注意：`.workbuddy/` 目录不要上传（含本地记忆）。本目录已可加 `.gitignore` 排除它。

### 2. 在仓库里配置 Secrets（邮件凭据）
进入仓库 **Settings → Secrets and variables → Actions → New repository secret**，添加：

| Secret 名 | 值 | 说明 |
|---|---|---|
| `QQ_EMAIL` | `790192539@qq.com` | 发件人 QQ 邮箱 |
| `QQ_AUTH_CODE` | 你的 QQ 邮箱**授权码** | 见下方获取方法，**不是登录密码** |
| `RECIPIENT` | `790192539@qq.com` | 收件人（可省略，默认发给自己） |

**获取 QQ 邮箱授权码：**
QQ 邮箱网页版 → 设置 → 账户 → 找到“POP3/IMAP/SMTP…” → 开启 **IMAP/SMTP 服务**
→ 按提示发短信验证 → 得到 16 位授权码，复制填到 `QQ_AUTH_CODE`。

### 3. 启用并测试
- 仓库 **Settings → Actions → General** 确认 Actions 权限为 `Allow all actions`。
- 进入 **Actions** 标签页，找到 “新股自动分析报告” workflow，点 **Run workflow** 手动跑一次验证。
- 验证邮件收到后，之后每周日北京时间 09:00 会自动跑 + 发邮件。

## 时间说明
- GitHub Actions 的 `cron` 是 **UTC**。已设为 `0 1 * * 0`（周日 01:00 UTC = 北京周日 09:00）。
- 云端触发可能有几分钟到几十分钟延迟，属正常。

## 注意事项
- **东财接口限流**：akshare 的东财接口在某些云 IP 上也可能被限流。脚本已内置回退：
  实时拉取失败时用仓库里的 `ipo_universe.csv` 缓存（workflow 每次成功运行会把它最新结果 push 回仓库）。
- **报告下载**：即使邮件没收到，每次运行也会在 Actions 的 “Artifacts” 里保留 `ipo-report`（30 天）。
- **本地也能跑**：不接 GitHub 时，本机 `pip install akshare pandas numpy` 后直接
  `python ipo_auto_report.py --n 10 --out ipo_report.html` 即可。

> 本报告/流程仅供参考，不构成个人投资建议。
