# 连接、安装与首次配置（Unreleased）

现有发布仍为 v1.0.1。本分支在该基线上增加 bootstrap，尚未发布；不要把它称为已存在的新 tag。Python 3.10+，无网络调用、pip/npm依赖安装、全局环境修改或自动更新。工具不安装软件本体，只分发本 Skill 并连接已有记忆空间。

## 从审核后的本地代码开始

下面尖括号是需要用户选择的绝对路径；脚本不会照抄占位路径。先确认 Python 可运行。解释器尚未安装时应明确报告缺 Python，不能假定脚本能自行引导安装解释器。

```text
python <skill>/scripts/bootstrap.py install --skills-root <existing-client-skills-directory>
python <skill>/scripts/bootstrap.py install --skills-root <existing-client-skills-directory> --apply
```

不带 `--apply` 只预览。安装根必须已存在且由用户明确选择，脚本在其下创建 `project-memory-sync`。已有内容逐字节相同则第二次运行不改任何文件；允许保留本地 `local-config.json` 和正常Python字节码。存在用户修改、额外资产或链接/junction则拒绝，绝不覆盖或删除。创建中失败会保留部分目录并返回失败，须检查该目录，不能当作成功安装或直接强制重试。

## 一次连接，同一记忆空间

```text
python <installed-skill>/scripts/bootstrap.py setup --workspace <existing-workspace> --archive <existing-archive>
python <installed-skill>/scripts/bootstrap.py setup --workspace <existing-workspace> --archive <existing-archive> --apply
python <installed-skill>/scripts/bootstrap.py doctor
python <installed-skill>/scripts/memory_sync.py search --query "合成植物提醒" --limit 5
```

默认配置保存于已安装Skill根 `local-config.json`；它被Git忽略且从部署ZIP白名单中排除。也可显式 `--config <new-local-config-file>`，其父目录必须已存在。不同客户端的代码副本可保有各自连接文件，但都指同一个已有档案；不复制档案。缺 workspace/archive 时一次返回全部缺项，无交互循环。可用 `--store-id` 检查期望库身份；不传时首次连接固定实际返回的UUID，后续身份改变即拒绝。

这些本地路径检查假定同机合作进程。它们不是抵御有权限的进程在检查与使用间替换路径的系统隔离；不要在攻击者可写的目录中安装或连接，也不保证网络盘与跨机器行为。

archive 必须在 workspace 内，有现成 schema3 的 `logs/s7-library.json`，不得在代码安装目录内；路径组件的 symlink/junction/reparse point 和多硬链接档案均拒绝。setup 不建档案、不迁移、不写业务记录。配置文件已存在时，语义相同返回 unchanged 且字节/mtime不变；不同则要求新配置路径，不覆盖既有配置。

完整连接也检查合作锁与备份目录的路径组件；既有锁、备份叶节点必须是单硬链接普通文件，备份根必须是普通目录，不能用预置junction把写入引到范围外。检查只取路径/类型/链接数，不读备份正文。每次通过完整连接调用CLI都会重验这些条件。

优先级沿用原CLI：显式 `--archive` > 进程 `PROJECT_MEMORY_ARCHIVE` > 显式 `--config` 内archive。没有显式archive、环境变量或config时，CLI才发现Skill根 local-config.json；doctor默认也读取它。有 connection_schema=1 的显式配置时，其他archive选择须与其一致。旧版仅含archive的配置仍兼容，但没有新增的身份/范围检查；请用setup创建新的完整连接。

默认 `access=read_only`。通过完整连接调用CLI时，apply/import/migrate均拒绝；migrate预览也会创建锁，所以只读连接同样拒绝。需要获授权写入时，使用明确的 `--access read_write --config <new-local-config-file>` 创建独立连接，写入请求仍需原有授权/幂等协议。该字段只约束使用此CLI连接的调用，不是OS沙箱；显式 `--archive` 或旧配置保留原行为，可绕过新增检查。只读doctor不探测写权限、不创建锁，`write_probe=not_performed`；它验证的是配置范围和可读身份，不能证明操作系统授予写权限。

## 客户端发现与打包

本工具只做通用本地复制，不承诺任何软件自动加载。Codex当前官方文档使用项目或用户 `.agents/skills`；bundled installer和旧版本可能使用不同目录，应按实际版本核对。WorkBuddy优先从技能UI上传本地ZIP，再检查启用与新会话调用。Hermes按实际profile目录配置。不能把复制成功当作自动发现或调用成功。路径来源和客户端限制见 [clients.md](clients.md)。

```text
python <skill>/scripts/bootstrap.py package --output <new-bundle.zip>
python <skill>/scripts/bootstrap.py package --output <new-bundle.zip> --apply
```

ZIP只含脚本内明确列出的20个通用文件，不扫描或归档整个工作树；固定目录前缀 project-memory-sync。新文件、配置、测试回执、runtime、数据库、备份、.git、凭据均不自动加入。既有输出拒绝覆盖。发布前还须审查白名单代码/文档内容和Git历史，白名单不能证明文本没有私人信息。

每个真实客户端另开新会话验收：发现Skill、报告实际代码目录、doctor同store_id、查询同条目；若要验证写入，只在同一个合成库中用read_write连接追加、再读回和幂等重放。当前仅提供可测试工具，不声称已通过实际客户端自动发现。
