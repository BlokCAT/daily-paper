# 每日论文推送（渲染 / 3DGS / 重建）

每天早上 6:30（北京时间）自动发一封邮件到你的 Outlook：
- 一篇**经典/里程碑论文**（渲染 → 3DGS → 重建 三类轮流）：标题、出处、链接、英文摘要、**DeepSeek 中文翻译 + 3 条核心贡献总结**、一句话"为什么值得读"
- 附 **5 篇 arXiv 最近提交**的相关新论文速递（标题+链接）

全部免费：GitHub Actions 免费运行，DeepSeek API 每天成本约 0.005 元。

---

## 一、准备工作（10 分钟，只做一次）

### 1. 一个能发邮件的邮箱（发件箱）

⚠️ **Outlook 不支持密码方式发信（微软已关闭基本认证），所以用 QQ 或 163 邮箱发信，收信才是你的 Outlook。**

QQ 邮箱开启步骤（163 类似）：
1. 登录 mail.qq.com → **设置 → 账号**；
2. 往下找 **「POP3/IMAP/SMTP…服务」** → 开启 **SMTP 服务**；lxiydvjpvwirgchc
3. 按提示发短信验证 → 得到一串 **16 位授权码**（不是 QQ 密码！记下来，这就是 SMTP_PASS）。

### 2. DeepSeek API Key

你已经有了，准备好在网页上粘贴即可（**不要发给任何人/任何 AI**，直接填到 GitHub 网页里）。

### 3. GitHub 账号

在 github.com 免费注册一个。

---

## 二、上传代码（5 分钟）

1. 网页打开 **github.com → 右上角 + → New repository**；
2. 名字填 `daily-paper`，**选 Public（公共仓库）**（免费时长无限；Private 仓库每月只有 2000 分钟配额，也够用但 Public 更省心）；
3. 创建后页面会显示上传方法，选 **"uploading an existing file"**，把本地 `每日论文推送` 文件夹里的全部文件拖进去（包含 `.github` 隐藏文件夹，Windows 资源管理器先开启"显示隐藏项目"），提交。

## 三、配置密钥（5 分钟）

仓库页面 → **Settings → Secrets and variables → Actions → New repository secret**，依次添加 6 个：

| 名称 | 值 |
|---|---|
| `DEEPSEEK_API_KEY` | 你的 DeepSeek API Key |
| `SMTP_HOST` | `smtp.qq.com`（用 163 就填 `smtp.163.com`） |
| `SMTP_PORT` | `465` |
| `SMTP_USER` | 你的 QQ/163 邮箱完整地址 |
| `SMTP_PASS` | 第一步拿到的 **16 位授权码** |
| `TO_EMAIL` | `blencatlar@outlook.com` |

## 四、测试 + 使用

1. 仓库页面 → **Actions** 标签 → 左边选 **daily-paper** → 右侧 **Run workflow** → 确认；
2. 等 1~2 分钟变绿勾，查收邮件（顺便看垃圾箱）；
3. 之后每天早上 6:30 自动运行，什么都不用管。

**改发送时间**：编辑 `.github/workflows/daily_paper.yml` 里的 cron（UTC 时间，北京时间 = UTC+8）。比如想 7:00 北京时间收到，填 `0 23 * * *`。

**加新论文**：编辑 `papers.json`，在对应分类（rendering / gaussian / reconstruction）数组里加一条：

```json
{"title": "论文标题", "year": 2024, "venue": "CVPR", "arxiv": "2406.xxxxx", "why": "为什么值得读"}
```

- 有 `arxiv` 字段 → 脚本自动从 arXiv 抓摘要；
- 老论文没有 arXiv → 脚本自动去 Semantic Scholar 按标题找摘要；
- 都抓不到 → 邮件只发题录和"为什么值得读"，链接自己搜。

## 五、本地试跑（可选）

```bash
pip install requests
python main.py --test    # 只打印邮件内容，不发信、不推进队列
```

## 常见问题

- **Actions 失败了？** 点开失败的那次运行，看 `Run daily paper` 步骤的日志，错误原因会打印出来；
- **收不到邮件？** 先查垃圾箱；再确认授权码没填成 QQ 密码；再确认 6 个 secret 名称与表里完全一致；
- **想暂停？** Actions 页面右侧 Workflow 详情里可以 Disable workflow。
