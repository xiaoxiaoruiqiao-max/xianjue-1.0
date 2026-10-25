# 先觉1.0 项目约定

这是 Python 标准库 + 原生 HTML/CSS/JavaScript 的本地网站。先读 README.md、UPDATE.md 和相关实现，说明必要假设，保持修改范围小，不引入未经要求的框架、API 密钥或云服务。

## 本地启动与验证

- 项目根目录就是包含 server.py 的本目录；所有数据路径相对于代码定位，不写死用户目录。
- Windows：`powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\start.ps1 -NoBrowser`。
- 其他系统：`python3 server.py --port 18789`。
- 默认只监听 127.0.0.1:18789。验证新副本时用不同端口，并检查 `/api/health` 的 project_root 确属新目录。不要停止未知服务。
- 首次启动会离线生成 dashboard。测试前运行 `python update.py --offline`，然后 `python -m unittest discover -s tests -v`。
- 有 Node 时运行 `node --test tests/test_frontend.cjs` 和 `node --check public/app.js`。不要仅凭进程返回码宣称页面可用；检查接口并实际打开网页。
- 网络失败保留旧记录与失败原因；静态快照可离线查看。无法核验的事项明确说明。

## 数据与修改边界

- 缺失保持缺失，不制造历史午间快照，不使用未来信息回填当时判断。
- 量价参考不等于八维综合；评分不等于收益概率。回评不冒充当时发布的信号。
- 保存原始评估、来源和修订版本。发布新评估使用 publish_review.py。
- 不覆盖或复制其他电脑的定时任务注册信息。schedule.json、日志、锁与进程号只留本机。
- 修改前设定可验证标准，修复后运行相关检查；不顺手重构无关功能。
- 网站不连接证券账户、不自动执行交易。
