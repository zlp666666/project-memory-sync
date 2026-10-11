---
name: project-memory-sync
description: 在用户现有项目与灵感档案中检索历史、保存明确要求收录的想法、同步已确认进展并追加探索经历。使用稳定 ID、来源证据和可重试事务；不建立另一套记忆库或自动创建正式项目。
---

# Project Memory Sync

本 Skill 是现有档案的操作接口。代码仓库不存私人资料。每个客户端使用同一个明确的 archive 绝对路径；该目录已有 `logs/s7-library.json`，它是本接口唯一写入正本。项目实际执行状态仍以其原有 `STATUS.md` / 业务系统为准。

首次接手读取用户指定工作空间规则、档案项目 README / STATUS，再运行：

```text
python <skill>/scripts/memory_sync.py --archive <archive> status
python <skill>/scripts/memory_sync.py --archive <archive> search --query "方向或名称"
```

未指定档案路径时只询问路径，不猜其他记忆目录。schema 不是 3 时禁止常规写入，阅读 [迁移与恢复](references/migration.md)，先展示迁移计划。客户端安装见 [clients.md](references/clients.md)，数据字段及命令见 [接口契约](references/contract.md)。

## 检索与判断

1. 先按完整名称、别名、关键词检索。名称相似不证明同一对象。用 `catalog` 分页查看候选，再由当前 AI 根据问题/目标/功能/来源作语义判断，提供含 ID 和理由的 `--semantic-file` 候选；脚本不会偷偷调用外部模型。它的词项相似度不是神经语义分数。
2. 确定 ID 后用 `history --id` 查看所有经历。不同名称描述同一对象，或多个历史片段属于同一方向时，必须得到用户明确合并确认，再用 `merge`。保留原 ID、原事件和重定向。
3. 回答历史问题时给出具体来源 ref、发生时间和证据状态；摘要不能冒充用户逐字原话，未知时间不能补造。区分“档案没找到”与“用户从没研究过”。停止原因只能引用记录；没有原因则说明尚无证据。

## 写入

只有明确收录指令或已经批准且涵盖此次 action/ID 的写入范围允许写入。普通问答、AI 自己建议、读取资料不产生授权。每个请求保留授权原话和引用；不把历史开放环变成当前待办。

- 一句话、没有成果也可收录。新想法用 `create`；相同对象再次讨论用 `append`，保留本次研究/尝试/失败/修改/暂停/重启的事件及用户原话。
- 已有项目新增功能用该项目 ID 的 `append`，事件 details 写 decisions/outcomes/problems/next_steps；后续事项只是对话记录，不是自动创建任务。禁止创建 kind=project 的正式项目卡。
- origin 分开记录 `user_proposed`、`user_approved`、`ai_suggested`、`knowledge_qa`、`uncertain`。用户认可 AI 提案必须保留 approval_quote。明确要求保存 AI 建议时可存 `ai_suggested`，不能写成已决定重启。
- depth（了解深度）、progress（历史实际进展）、intent（当前处理意向）、evidence_status（证据）独立。收录不表示实施。改变当前意向用 `decision` 和用户授权。
- 写入前定位/查重。精确同名或别名候选必须追加、或有明确“同名不同对象”确认才允许独立新建；近似候选由人/AI 核对，不自动合并。

把请求保存为档案项目内临时 JSON（不放 Skill 仓库），运行 `apply --file <request.json>`。沿用同一个 request_id 和相同内容重试，冲突时停止并重新读取，不生成新 ID 盲目重试。按 [示例](examples/create.json) 的协议构造；仓库根 `examples/` 全为合成数据。

成功后报告实际返回的 record_id、saved_path、revision 和 actual_update。失败不宣称保存完成。恢复和导入必须核对当前 revision；不要直接编辑正本或复制旧快照覆盖新更新。并发保证仅覆盖同机通过本接口写入的客户端，不能覆盖绕过接口的手工编辑、网络盘或多台机器。
