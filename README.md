# NovelCollector 在线更新

这是 NovelCollector 的公开发行仓库。安装包及更新包可以直接下载，无需 GitHub 登录、令牌或 GitHub CLI。源码仓库继续保持私有；发行包中包含运行所需的 Python 代码。

下载最新版：[Releases](https://github.com/764s/NovelCollector-updates/releases/latest)。Windows 用户下载 `windows-x64.zip`，解压后运行 `NovelCollector.exe`；Linux 用户下载 `linux-x64.tar.gz`。

从 2.5.2 开始，网页中的“检查更新 → 更新并重启”直接使用本仓库。旧客户端仍指向私有仓库，本次请换用新的完整包一次，同一系统用户的数据目录保持不变。

每次发布先在草稿中准备五项资产，发行工作流在 Windows、Linux 上核对摘要并验证安装、备份、回退和包内测试，随后发布。发布后再从未登录的原生环境匿名下载实际发行包，验证在线安装和重启。通过后的回执位于 `release-status/版本` 分支。

`qa` 中仅有发行验证脚本，不含私有源码仓库的历史、用户数据或凭据。
