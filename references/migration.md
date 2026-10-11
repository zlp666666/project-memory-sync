# 旧库迁移、版本更新与恢复

## 旧 S7 迁移

先备份已有 s7-library、构建脚本和计划修改的交付文件；不要修改原客户端数据库。`migrate` 默认计划模式读取现有 s7-library、batches、inventory、project-index，不调用模型。

```text
python <skill>/scripts/memory_sync.py --archive <archive> migrate
python <skill>/scripts/memory_sync.py --archive <archive> migrate --apply --authorization-file <authorization.json> --expected-plan-hash <returned hash>
```

必须先展示计划与缺陷，再在既有批准范围内执行独立的 apply 阶段。完整源快照指纹变化会拒绝旧计划。先通过隔离数据测试，再迁移真实库。再次 migrate 识别schema3并返回 duplicate=true，不重建稳定 ID。

迁移处理五个缺陷：

- 旧 uN 的可见顺序与导出顺序各保留一份桥接，旧 localStorage 的决定不自动接受。新版所有视图复用 UUID。
- 孵化 nN 映射到完整库同一 UUID，新看板统一导出对象，避免精选选择漏掉。
- 恢复 batches 所有有名构想的独立来源对象，去重依据完整会话身份与完整对象；不同经历不按同名丢弃。
- 旧深度、归属、投资量仅放 legacy_claims；当前深度 unknown，待逐条证据修正，避免正则猜测冒充已验证。
- 会话内多个项目只是 unverified_project_mentions，不把每条 idea 自动挂给每个项目。历史项目汇总单列为 kind=project、formal_project_card=false。

旧 library 完整放 legacy_data，事件提供摘要与 ref，原提取对象保留。来源中的“用户提出”等仍是抽取主张，证据 unverified；摘要没有实际逐字原话时 quote 为空。不会重新扫描客户端，也不会自动创建正式项目卡/任务。不是重新人工核验全部历史。

在项目内生成新命名的 schema3 看板；保留旧 HTML/MD 为历史证据，并在项目 README/STATUS 中指向新入口。旧构建器必须在发现 schema3 时停止，不能把正本降回旧 schema。若以后需要重跑历史管线，请先将输出定向到新的隔离文件、审阅后由 import 合入。

## 操作回滚

rollback 采用补偿事件，需 expected_revision，限最新 apply。保留失败/误写证据和 request_id 回执；后续写入已发生就拒绝回滚，不能损失另一个 AI 的更新。

```json
{"request_id":"synthetic-rollback-001","action":"rollback","target_request_id":"synthetic-original-001","expected_revision":2,"authorization":{"basis":"explicit_user","reference":"synthetic:user-2","quote":"撤回刚才这次更新。"},"source":{"ref":"synthetic:user-2","quote":"撤回刚才这次更新。","evidence_status":"verified"},"text":"撤回最近一次误写"}
```

用 verify-backup 校验返回的哈希。完整快照恢复先在独立模拟目录复制并验证，再对照当前修订号/后续事件做审阅。schema迁移整库回退是离线维护：停止所有写入，核对当前库哈希，备份当前库，明确批准恢复指定快照，先还原到一个新的路径验证；不提供会静默覆盖后续事件的一键整库回退。

## Skill 版本更新

1. 所有客户端完成当前事务，记录 status 的 store_id/revision 和自己的 Skill tag。
2. 下载可信仓库的新固定 tag 到一个新的代码目录，查看 CHANGELOG 和schema兼容说明，不执行远程安装脚本。
3. 在隔离样例运行 `python -m unittest discover -s tests -v`。用既有数据脱敏副本做读写、并发和恢复测试。
4. 数据快照不提交 Git。只有版本要求 schema 迁移时，先 plan 再 apply；1.0.0内不自动迁移已是schema3的库。
5. 单个客户端切换代码版本，完成跨客户端交接验收，再逐个切换。回退代码使用上一个 tag，数据回滚与代码回滚分开。

依赖标准 Python，无需升级全局配置。代码版本回退不代表数据已回退。迁移或导入遇到冲突即停止并报告，不自动重试不同参数。
