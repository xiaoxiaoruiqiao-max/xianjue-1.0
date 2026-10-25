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

实现版本 `4eb5098` 已完成以下验收，随后发布文档和截图不改变程序逻辑：

| 验收项 | 实际结果 |
| --- | --- |
| 从 GitHub 重新克隆 | 从远端取得独立副本，提交与仓库一致，不使用原目录的运行文件 |
| 干净 Python 环境 | Python 3.12.14，使用 `venv --without-pip`，不安装第三方包即可启动 |
| Windows 启动文件 | PowerShell 与 `启动先觉1.0.cmd` 均通过；重复启动可识别本目录服务 |
| 中文与空格路径 | 本地独立目录和 GitHub Windows 环境均通过 |
| 首次离线启动 | 不携带 dashboard、schedule、运行日志；自动生成页面数据，定时任务显示待配置 |
| Python 回归 | 56 项通过，含新目录真实 HTTP 服务测试 |
| 前端回归 | 10 项通过；JavaScript 语法检查通过 |
| GitHub 全新环境 | Windows、Ubuntu 两个任务均通过，见下方运行记录 |
| 数据迁移一致性 | 独立克隆的 988 个受版本管理的数据文件与原目录逐字节一致 |
| 页面实际检查 | 首页、历史月份、冲突日期、午间空状态、返回最新交易日、数据状态弹窗均正常；控制台无错误或警告 |

测试运行：[Verify local deployment #40205489098](https://github.com/xiaoxiaoruiqiao-max/xianjue-1.0/actions/runs/40205489098)。后续每个提交仍会执行相同检查，以其 Actions 结果为准。

验收副本显示 241 个交易日、238 份完整八维评估，行情最新日期为 2026-10-23。测试在 Codex 当前本地执行环境中完成；另外使用 GitHub 的 Windows、Ubuntu 新环境复验，未在另一台个人电脑上实测。

迁移过程中实际发现并修复：固定端口妨碍独立副本验收、Windows PowerShell 误读中文 Python 路径、历史发布锁仅支持 Windows。原始金融资料不因这些兼容修复而改写。

下图为从 GitHub 克隆并在本机启动的实际页面：

![GitHub 克隆副本的实际首页](docs/github-clone-home.jpg)

## 验证边界

运行与数据检查不证明评分有收益预测能力。历史回评不等于当时发布过的信号；缺失的午间快照、精确风险统计及三个冲突日期保留原状。没有在所有用户电脑上实测，也不能保证第三方公开行情接口永远可用。

## 给新电脑上的 Codex

打开本仓库的本地项目后，可以直接发送：

> 请读取 AGENTS.md 和 README.md，检查 Python，然后在这台电脑启动先觉1.0。先保留仓库中的历史记录，不创建定时任务。若默认端口被其他项目占用，使用空闲端口，验证 /api/health 的 project_root 属于这个目录，再打开网页检查。

Codex 使用本地文件夹的官方说明：https://learn.chatgpt.com/docs/projects 。
