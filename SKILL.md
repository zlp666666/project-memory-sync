---
name: project-memory-sync
description: 检索已有项目与灵感档案，按明确授权收录或追加来源历史，或从本机记忆空间接手项目。使用稳定ID和可重试事务，不自动保存普通问答或创建正式项目。
---

# Project Memory Sync

这是通用记忆工具与本机现有记忆空间的连接接口。代码包不包含私人记忆；各客户端连接同一个已有 archive，不各自建库。项目 `STATUS.md` 是唯一当前状态，档案保存来源历史，不维护第二份当前状态。

遵守适用工作空间规则；已经加载且未变化的不重读。连接信息未知时运行 `python <skill>/scripts/bootstrap.py doctor`。已保存的 `local-config.json` 自动读取，也支持显式 `--config`；缺项只集中询问一次 workspace/archive，首次配置才读[连接与安装](references/setup.md)。无路径就停在准确缺项，不猜目录、不创建档案。doctor 是短摘要，核对 store_id/schema/revision；配置的身份或路径冲突时停止。

- **接手项目**：仅此任务读[接班入口](references/handoff.md)。先定位指定项目的 README/STATUS；历史按需查询，不把旧开放环自动变成当前待办。
- **查询历史**：`python <skill>/scripts/memory_sync.py --config <connection> search --query "名称或关键词" --limit 5`。已有默认连接可省略 `--config`。需要时 `catalog --offset N --limit 5`；确认稳定ID后 `history --id UUID`。本基线 history 返回该对象完整历史，可能很长，只在确有需要时执行。脚本仅给词项候选，语义由当前AI判断；没找到不代表从未研究过。
- **明确授权写入**：先查重定位，再按[契约](references/contract.md)构造 action/ID/授权原话/来源/request_id；需要时读合成[示例](examples/create.json)。配置默认为 read_only，须另行明确选择 read_write 连接。`apply --file <request>` 通过统一接口写入，不手改正本；同一请求重试保留原request_id，冲突停止。合并须有人明确确认同对象。
- **安装/更新/排错**：仅此任务读[连接与安装](references/setup.md)及[客户端边界](references/clients.md)。迁移/导入/回滚才读[迁移恢复](references/migration.md)，不默认读全部资料。

历史回答保留来源ref/时间/证据状态，摘要不冒充逐字原话，未知原因不补造。普通问答、读取和AI建议不产生写入授权。成功写入后报告实际 record_id、saved_path、revision、actual_update；没有成功回执不称已保存。只读连接检查是CLI协作保护，不是操作系统权限；显式 `--archive` 的旧用法保留原行为。跨机同步、客户端自动发现及正式STATUS安全写回均未由本包验证。
