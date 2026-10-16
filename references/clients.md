# 跨客户端适配与安装

最后核对：2026-10-12。本文提供安装方法，本次开发没有在 WorkBuddy、Codex、Hermes 或 ChatGPT 中安装本 Skill，也没有修改全局配置。官方文档、只读本机检查和客户端完整验收是三个不同层次。

2026-10-16 Unreleased增量：通用本地安装、连接与自检见[setup/doctor](setup.md)。该工具使用明确的skills-root，不猜客户端发现目录，不安装软件本体。以下原有本机版本检查属2026-10-12历史记录；本分支测试只用合成目录，没有真实客户端安装/新会话发现结果。

当前官方能力边界：Codex [Build skills](https://learn.chatgpt.com/docs/build-skills)列出项目/用户 `.agents/skills`；部分bundled installer默认仍为 `$CODEX_HOME/skills`，故应明确安装目标并实测发现。[插件onboardingSkill](https://developers.openai.com/plugins/build/plugins)是用户触发的安装后设置入口，本仓库没有因此成为插件。WorkBuddy [技能说明](https://www.codebuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/Skills-Market)支持UI上传本地技能包，对话安装限技能市场，不宣称任意GitHub URL对话安装。[项目说明](https://www.codebuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/Project)另有代码项目 `.codebuddy/skills` / rules / AGENTS支持；不等于本机已发现本包。Hermes本轮未复核，下面profile说明应在使用前按实际版本确认。

## 共同约定

- Skill 安装目录只保存通用代码、说明和模拟示例。私人档案仍在原有档案目录中，各客户端指向同一个目录；不得复制为各自的长期记忆库。
- 运行环境为 Python 3.10 或更高版本，核心仅使用标准库。客户端必须能够运行本地脚本，并且实际获得档案目录的访问权限。
- Git 仓库、安装成功、Skill 被发现、Skill 被调用、数据写入成功必须分别验收。看到 `SKILL.md` 不等于全部安装完成。
- 先核对已批准的仓库地址、可见性、版本标签及对应提交，再安装。以下 `<...>` 必须替换为已确认的值；示例不包含任何真实私人路径。
- 相同版本可以在各客户端安装不同的代码副本，但数据路径必须相同。不使用链接拼装私人档案，不自动运行远程安装脚本。

先在已审核的代码目录运行离线检查：

```powershell
$skillDir = '<REVIEWED_SKILL_CHECKOUT>'
$archiveDir = '<APPROVED_EXISTING_ARCHIVE_DIRECTORY>'
python --version
python (Join-Path $skillDir 'scripts\memory_sync.py') --archive $archiveDir status
```

`--archive` 指向包含 `logs/s7-library.json` 的现有档案目录。本分支已有完整连接时优先用 `bootstrap.py doctor` 返回短身份检查；缺连接时按[setup说明](setup.md)集中提供workspace/archive。安装检查不运行迁移。`migrate` 即使不带 `--apply` 也会创建合作锁文件，不是无副作用的只读检查；只在明确迁移任务中按[迁移说明](migration.md)使用。也可使用本机未提交的 `local-config.json` 和 `--config`，或仅对当前进程设置 `PROJECT_MEMORY_ARCHIVE`。不把实际路径配置提交 Git，不修改全局环境变量。

日常调用示例：

```powershell
python (Join-Path $skillDir 'scripts\memory_sync.py') --archive $archiveDir search --query '模拟想法'
python (Join-Path $skillDir 'scripts\memory_sync.py') --archive $archiveDir catalog --limit 10
python (Join-Path $skillDir 'scripts\memory_sync.py') --archive $archiveDir history --id '<STABLE_RECORD_ID>'
python (Join-Path $skillDir 'scripts\memory_sync.py') --archive $archiveDir apply --file '<APPROVED_REQUEST_JSON>'
```

写入请求结构、授权与幂等键必须遵守本 Skill 的接口契约。客户端不得直接改数据库或 JSON，不得用自己生成的另一个数据库替代统一接口。

## Codex

**文档已核实。** 官方说明 Codex 会从仓库或用户的 `.agents/skills` 等位置发现 Skill，并支持 `$skill-installer`。本机当前会话的已提供技能目录使用 `.codex/skills`；这只能证明本机会话能发现现有目录，不能证明所有版本的发现目录一致。[OpenAI：Build skills](https://learn.chatgpt.com/docs/build-skills)

固定版本的用户级安装示例，目标目录必须尚不存在：

```powershell
$approvedRepoUrl = '<APPROVED_REPOSITORY_URL>'
$approvedVersionTag = '<APPROVED_VERSION_TAG>'
$codexSkillsRoot = Join-Path $env:USERPROFILE '.agents\skills'
$codexSkillDir = Join-Path $codexSkillsRoot 'project-memory-sync'
if (Test-Path -LiteralPath $codexSkillDir) { throw '目标目录已有内容，请按更新流程处理。' }
New-Item -ItemType Directory -Path $codexSkillsRoot -Force | Out-Null
git clone --branch $approvedVersionTag --single-branch $approvedRepoUrl $codexSkillDir
git -C $codexSkillDir rev-parse HEAD
```

如果当前客户端不发现此位置，使用该客户端提供的 `$skill-installer`，明确给出仓库、Skill 所在路径与固定版本，并要求它返回实际安装路径。不要把另一版本文档中的目录猜测为已生效配置。

安装后开启新会话，明确调用 `$project-memory-sync`，要求先执行 `status`、检索一个隔离示例，再报告加载的 `SKILL.md`、代码版本及档案路径。最终成功以调用结果为准；必要时重启客户端。云端 Codex 不会因为本地安装就自动获得本机档案。[OpenAI：Codex environments](https://learn.chatgpt.com/docs/environments/modes)

## WorkBuddy

**文档已核实，本地导入待验收。** 官方路径是「技能 → 添加技能 → 上传技能」，选择本地技能包。导入后需在已安装列表检查，并确保启用。本机只读检查发现 WorkBuddy 5.7.7 和技能目录；没有向应用导入本包。[WorkBuddy：技能](https://www.workbuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/Skills-Market)

从已审核的 Git 标签生成 ZIP，归档只取已提交文件，不包含工作目录中的本地路径配置和私人数据：

```powershell
$skillCheckout = '<REVIEWED_SKILL_CHECKOUT>'
$approvedVersionTag = '<APPROVED_VERSION_TAG>'
$bundleZip = '<NEW_RELEASE_ZIP_PATH>'
if (Test-Path -LiteralPath $bundleZip) { throw '输出文件已存在，请指定新的路径。' }
git -C $skillCheckout archive --format=zip --prefix=project-memory-sync/ --output=$bundleZip $approvedVersionTag
```

上传前检查 ZIP：应包含 `project-memory-sync/SKILL.md`、`scripts/` 和引用文档，不得含 `.git`、私人数据、凭据或本地配置。导入后在新会话中指定使用 `project-memory-sync`；要求读取实际安装目录中的脚本，并显式传入同一档案路径执行 `status`。不要仅凭复制进 `.workbuddy/skills` 宣称已安装。

开放平台的市场提交格式另要求 `description_zh`、`description_en`、`version`、`author` 等字段；标准通用 Skill 的最小格式与市场提交要求不能混为一谈。本次不进行市场提交。若本地 ZIP 导入器报告元信息或目录结构错误，应保存错误，再按该版本的导入要求制作单独适配包，保留相同核心脚本；不要直接更改通用数据契约。[WorkBuddy 开放平台：技能结构](https://open.workbuddy.cn/docs/skill)

## Hermes

**文档及只读 CLI 检查已核实，Skill 调用待验收。** 官方支持 `hermes skills list`、`inspect`、`install`，Skill 位于当前 profile 的 `HERMES_HOME/skills`。本机 `hermes --version` 返回 v0.21.1（2026.9.7），`hermes skills install --help` 实际存在；没有安装 Skill。[Hermes：Skills System](https://hermes-agent.nousresearch.com/docs/user-guide/features/skills/)

先确认当前 profile 的数据根目录。Windows 官方默认数据位置为 `%LOCALAPPDATA%\hermes`，POSIX 默认为 `~/.hermes`；设置 `HERMES_HOME` 或使用命名 profile 时以实际 profile 为准。[Hermes：Installation](https://hermes-agent.nousresearch.com/docs/getting-started/installation/)

本仓库的 `SKILL.md` 位于根目录。为固定发布版本，采用 Git 克隆至当前 profile 的技能子目录，目标必须尚不存在：

```powershell
$approvedRepoUrl = '<APPROVED_REPOSITORY_URL>'
$approvedVersionTag = '<APPROVED_VERSION_TAG>'
$hermesProfileRoot = '<ACTIVE_HERMES_PROFILE_HOME>'
$hermesSkillDir = Join-Path $hermesProfileRoot 'skills\project-memory-sync'
if (Test-Path -LiteralPath $hermesSkillDir) { throw '目标目录已有内容，请按更新流程处理。' }
New-Item -ItemType Directory -Path (Split-Path -Parent $hermesSkillDir) -Force | Out-Null
git clone --branch $approvedVersionTag --single-branch $approvedRepoUrl $hermesSkillDir
git -C $hermesSkillDir rev-parse HEAD
hermes skills list
```

命名 profile 的发现检查使用对应的 `hermes -p <PROFILE_NAME> skills list`。新会话中调用 `/project-memory-sync`，核对实际 Skill 路径，再按共同约定执行检索。本机源代码和帮助显示 GitHub 安装标识为 `owner/repo/path-to-skill`；不要把仅 `owner/repo` 或未经核实的 `@tag` 当作本包的版本锁定安装命令。外部技能目录是可选适配，需要修改客户端配置，本次不配置。

Hermes 的本地、Docker、SSH 等执行后端未必看到同一路径。运行时必须核对档案确实位于获授权的本机目录；远程机器上不存在本机盘符时，应交接回本地执行，不能另建权威副本。Skill 被加载后会暴露实际技能目录，可据此调用脚本。[Hermes：Creating Skills](https://hermes-agent.nousresearch.com/docs/developer-guide/creating-skills/)

## ChatGPT

**提供交接方案，没有宣称已安装。** ChatGPT 工作区 Skill、客户端本地文件 Skill 和插件有不同的安装与权限边界；上传 Skill 不会自动授予本地文件权限。[OpenAI：Skill controls](https://learn.chatgpt.com/docs/enterprise/skills)

普通网页对话或云端任务不能仅凭一个本地盘符路径访问电脑档案。桌面本地 Work/Codex 或获批准的已连接本机执行能力，需要另行确认其可用性和访问范围。即便云端能读取上传文件，上传副本也不是原档案的权威数据源。[OpenAI：Use ChatGPT](https://learn.chatgpt.com/docs/use-chatgpt)

当前可行方案：

1. ChatGPT 在用户明确要求保存时，整理最小写入请求：用户原话、历史来源、来源类型、确认范围、目标稳定 ID（若已知）和幂等键。只分享任务必要内容。
2. 将该请求交给已安装本 Skill 且获授权访问原档案的本地 Codex、WorkBuddy 或 Hermes。请求可作为文件或复制文本交接；不能只说“已保存”。
3. 本地执行端重新查重与定位，通过接口写入，返回条目 ID、实际路径、更新内容、操作结果及不确定性。
4. ChatGPT 只有收到执行端的成功回执后才能说已保存。若无法访问本地执行端，保留为待执行请求。

将来可提供受控接口或 MCP 服务，但本版本没有部署服务器，不默认暴露档案，也没有验证 ChatGPT 云端直写能力。

## 每个客户端的独立验收

用户确认仓库与版本后，各 AI 分别安装。先使用同一份隔离模拟档案；完成后才做已获授权的真实有限试写。以下不是本次已经完成的客户端验收记录。

| 检查 | 通过证据 |
| --- | --- |
| 版本与发现 | 新会话报告同一版本、对应提交、实际 `SKILL.md` 和脚本目录 |
| 共同数据源 | 各客户端 `status` 返回同一档案标识、规范化路径、格式版本与修订信息 |
| 新会话检索 | 能用名称和别名找到另一客户端保存的模拟条目，并返回相同稳定 ID |
| 跨客户端追加 | 第二个客户端追加一次经历；第三个客户端能读到完整历史，条目总数不因追加增加 |
| 重复调用 | 同一请求和幂等键再次执行，返回原结果且没有新增事件 |
| 并发与失败 | 两客户端同时追加均保留；无授权或无权限请求失败后数据保持完整 |
| 受限客户端 | 看不到原档案时明确报告待交接，不生成另一套长期数据库 |

验收记录至少保留：客户端版本、Skill 版本与提交、执行环境、模拟档案标识、请求 ID、结果 ID、实际保存路径和检查时间。真实路径及真实回执只存本地私人项目，不提交 Skill 仓库。

## 更新与回退

1. 先阅读新版本变更说明，核对 Python 要求与数据格式兼容性；运行隔离测试。
2. 保留旧代码版本，审核并取得新发布标签。只更新 Skill 代码，不把私人数据纳入 Git 操作。
3. Codex/Hermes 的 Git 副本先检查本地改动；有改动时停止并备份，不能强制覆盖。没有改动时 `git fetch --tags`，核对提交后 `git checkout --detach <APPROVED_VERSION_TAG>`。
4. WorkBuddy 按新标签重新生成并检查 ZIP，按该版本 UI 的导入/更新方式执行。先核实是否支持替换，保留旧包和客户端验收记录。
5. 每个客户端开新会话重做发现、检索和重复调用检查。代码回退到旧标签不等于档案回滚；数据回滚必须通过接口契约执行，并核对兼容性及最新修订，避免撤销其他 AI 的后续更新。

本文中的安装命令只下载或复制已审核代码，不执行远程安装脚本。私人档案的迁移、导入和回滚仍由统一接口处理，并遵守其授权、备份和修订检查。
