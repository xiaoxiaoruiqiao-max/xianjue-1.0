# 先觉1.0：迁移与部署验证

本次验证针对可从 GitHub 重新取得的完整项目，不包含之前讨论中的持仓工作台概念图。验证日期：2026-10-25，北京时间。

## 可迁移条件

- Python 3.10+；网页运行只用标准库，前端不依赖 CDN。
- Windows 双击 `启动先觉1.0.cmd`；其他系统可运行 `python3 server.py`。
- 数据按项目目录定位，中文和带空格的路径也纳入测试。
- 首次启动不需要网络，会根据随仓库保存的行情、评估与交易日历生成页面数据。
- 网络只用于更新公开资料。未取得新资料时保留旧记录和时间，不表示已更新到当前交易日。
- 不携带原电脑的定时任务、日志、锁、进程号或凭证。新电脑需另行配置定时研究更新。

## 自动验证覆盖

`python update.py --offline` 后运行 `python -m unittest discover -s tests -v`：

- 财经数据、评分、时间边界与来源规则回归。
- 将源码与数据复制到新的中文/空格目录，从不同工作目录启动真实服务。
- 使用独立端口验证本次副本的 `project_root`，避免误认已有服务。
- 无 dashboard、无 schedule、无更新日志的首次初始化。
- 首页、JavaScript、CSS、图标和数据接口。
- Host/Origin 限制、非法参数、内部文件不能通过网页读取。
- 已发布历史评估引用的来源缓存完整。

`node --test tests/test_frontend.cjs` 检查前端状态与日期逻辑，`node --check public/app.js` 检查语法。

GitHub Actions 的 `Verify local deployment` 工作流在 Windows、Ubuntu 上检查同一版本，Windows 额外执行 PowerShell 启动器。每次提交的实际结果以 Actions 页面为准。

## 本次交付验收

本地迁移回归已先复现旧版无法在新端口就绪的问题，修复后通过。GitHub 重新克隆、干净 Python 环境、启动器及浏览器验收结果将在完成后记录于本节。

## 验证边界

运行与数据检查不证明评分有收益预测能力。历史回评不等于当时发布过的信号；缺失的午间快照、精确风险统计及三个冲突日期保留原状。没有在所有用户电脑上实测，也不能保证第三方公开行情接口永远可用。

## 给新电脑上的 Codex

打开本仓库的本地项目后，可以直接发送：

> 请读取 AGENTS.md 和 README.md，检查 Python，然后在这台电脑启动先觉1.0。先保留仓库中的历史记录，不创建定时任务。若默认端口被其他项目占用，使用空闲端口，验证 /api/health 的 project_root 属于这个目录，再打开网页检查。

Codex 使用本地文件夹的官方说明：https://learn.chatgpt.com/docs/projects 。
