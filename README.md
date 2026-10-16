# project-memory-sync

已发布基线 **1.0.1**；本分支含 **Unreleased 安装与接班增量**，尚未发布新tag。面向多 AI、单台本地电脑的项目与灵感档案统一接口，遵循 [Agent Skills](https://agentskills.io/specification) 结构。唯一数据源是用户现有档案 `logs/s7-library.json`；代码和私人数据分离，不创建另一个记忆数据库。

支持明确指令的一句话灵感、已有项目进展、追加探索经历、名称/别名候选、当前 AI 参与语义判断、可溯源历史、人工确认合并、稳定 ID、幂等请求、备份、最新操作补偿回滚、增量导入导出及本地并发写入。

## 目录与依赖

```text
SKILL.md                AI操作指引
scripts/core.py         原子事务、查重、回放、并发、回滚
scripts/legacy.py       保守修复旧S7身份和来源
scripts/memory_sync.py  JSON命令行接口与稳定ID看板
scripts/privacy_check.py Git文件和历史发布检查
references/             字段契约、迁移、客户端适配
examples/               合成请求（不含私人数据）
tests/                  隔离验收与故障测试
VERSION / CHANGELOG.md  版本与兼容说明
```

Python **3.10+**，运行时无第三方包。开发/分发需要 Git。以下测试在开发仓库执行；bootstrap部署ZIP不包含测试目录：

```text
python -m unittest discover -s tests -v
python scripts/memory_sync.py --version
python scripts/privacy_check.py
```

可选 Node.js 用于一项生成看板的真实事件处理器测试；缺少 Node 时该项明确跳过，其他测试仍只需 Python。Skill frontmatter 的开发验证器可用 PyYAML；它不属于运行时依赖。测试记录与 A–I 对应关系见 [验收记录](references/validation.md)。

## 使用

首次安装与本机记忆空间连接见[setup/doctor](references/setup.md)。默认预览、显式路径、已有内容保护、默认只读连接；可打包通用代码，不包含私人记忆。项目接班见[按需入口](references/handoff.md)，不自动修改项目STATUS。实际客户端发现仍须逐个验收。

先完成 [安装与验收](references/clients.md)，指向已有档案。不要在代码仓库内创建私人库。

```text
python scripts/memory_sync.py --archive "<existing archive>" status
python scripts/memory_sync.py --archive "<existing archive>" search --query "plant reminder"
python scripts/memory_sync.py --archive "<existing archive>" apply --file "<private request.json>"
```

状态不是schema3时按 [迁移说明](references/migration.md) 先计划、检查、备份再迁移。不能直接接入旧错配 ID。字段、例子和全部命令见 [接口契约](references/contract.md)。

## 实际边界

- Skill 代码测试通过不代表每个 AI 已成功安装；须逐客户端在新会话验证加载、同 store_id、同路径和交接结果。
- 搜索的脚本层为词项候选，语义由调用 AI 提供；未内置神经 embedding，完全无人参与的跨词义检索未支持。
- 并发限同机本地文件及本接口协作写入。人工直接改正本、网络盘、多机离线冲突不在保证内。
- 历史原话缺失、抽取偏差、未知日期/来源关联保持不确定；不会把旧“暂停”变成当前待办。
- 不创建正式项目卡、不改全局规则、不安装客户端、不发布私人数据。
- 授权参数是审计契约，不是用户身份认证。所有用户对文件系统的实际权限仍由客户端/系统控制。

## 发布与更新

发布前运行测试与隐私检查，只提交通用代码、文档和合成样例。固定 tag 分发，客户端逐个更新。查看 [更新与恢复流程](references/migration.md)。远程仓库归属和可见性未确认时只创建本地仓库/分发包；不推送、不创建公开仓库。
