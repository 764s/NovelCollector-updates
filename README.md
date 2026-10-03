# NovelCollector 在线更新

此仓库只处理 NovelCollector 的在线更新，发布 app-only 更新 ZIP 和对应的 `.sha256` 校验文件。软件检查和下载更新无需 GitHub 登录、令牌或 GitHub CLI。

首次安装、重新安装和完整发行说明请到 [NovelCollector Releases](https://github.com/764s/NovelCollector/releases/latest)。Windows、Linux 完整安装包在原仓库发布。

从 2.5.2 开始，软件网页中的“检查更新 → 更新并重启”直接使用本仓库。旧客户端请先从原仓库安装新完整包一次。

工作流仅接受两项更新文件，完整安装包会被拒绝。更新候选文件位于 `candidate/版本` 分支；在 Windows、Linux 使用 CI 的 Python 验证安装、备份、回退与测试后发布，再验证匿名在线检查、下载、安装和重启。通过后的回执位于 `release-status/版本` 分支。

原生启动器、内置运行时和完整包的验收在原仓库完成。公开更新包包含应用 Python 代码，`qa` 保存更新验证脚本。

测试范围：Linux 运行 258 项应用测试，Windows 运行 67 项更新相关测试。Windows 全套应用测试中已有的路径、资源释放与抓取超时问题不计入已通过的更新验收。
