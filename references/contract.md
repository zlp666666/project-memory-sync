# 数据与接口契约 v1

适用：Skill 1.0.0，Python 3.10+，本地磁盘，单台机器。全部运行时依赖为 Python 标准库。没有远程服务、账号、模型 API、嵌入下载或新数据库。

## 权威与身份

`--archive` 指向既有考古档案目录，包含 `logs/s7-library.json`。schema 3 在同一个文件中维护 records、events、operations、redirects、migration、legacy_data；不把 Skill checkout 当成记忆目录。旧 project-index、batches、报告为来源资料，Skill 不将其复制进代码仓库，也不声称汇总项目卡等同真实工程进展。

新记录为 UUID4；迁移记录为 UUID5(namespace + SHA256(完整来源对象))，来源对象含客户端/会话 UID/完整提取对象。UUID 在首次入库后永久保留。内容更新不能重新算身份。同名、排序和数组位置不决定身份；旧 uN/nN 只作审计桥接，不能直接执行历史选择。重复源提取保留全部引用及上下文变体。

## 字段

| 字段 | 取值 / 含义 |
|---|---|
| kind | idea / project（project 仅指导入的已有历史项目，不能由 create 建正式项目） |
| origin | user_proposed / user_approved / ai_suggested / knowledge_qa / uncertain |
| depth | idea / inquiry / research / prototype / unknown |
| progress | unknown / discussed / researched / developed / failed / paused / completed / abandoned |
| intent | undecided / archive / keep / restart / join_existing |
| evidence_status | verified / unverified / inference |
| aliases | 全称别名列表；用于候选查找，不自动确定同一对象 |

events 保存 ID、record_id、kind、text、occurred_at、recorded_at、source、changes、authorization、actor、request_id；历史导入发生时间可未知，source.quote 为空且 quote_kind=extracted_summary。新写入保留 source.quote 和授权 quote 的真实原话，不把模型摘要填成逐字引用。details 可记录 decisions/outcomes/problems/next_steps，系统不会将它们转成任务。

## 命令

参数在子命令前：`python <skill>/scripts/memory_sync.py --archive <archive> <command>`。
可用 `--config <local-config.json>` 读取 `{ "archive": "<absolute path>" }`；只读配置，不写全局环境。亦支持当前进程 `PROJECT_MEMORY_ARCHIVE`。路径必须指向现有目录，禁止数据正本在代码仓库内。

| command | 参数 | 行为 |
|---|---|---|
| status | 无 | schema、库 ID、版本、修订号、迁移审计 |
| catalog | --offset N --limit N | 分页摘要，供 AI 判定语义；不回传聊天正文 |
| search | --query TEXT --limit N [--semantic-file FILE] | 名称、别名、词项候选；可组合 AI 语义候选 |
| history | --id UUID | 原始或重定向 ID 的全部事件、来源及限制 |
| apply | --file FILE | 一个原子请求；成功 JSON，失败 exit 2 + ok=false |
| export | --output FILE | schema3 完整脱机快照（含私人数据），拒绝覆盖已有文件 |
| import | --file FILE --expected-revision N --authorization-file FILE | 同 store_id 增量导入；有分歧就拒绝，不覆盖 |
| render | --output NEW.html | 新版稳定 ID 的只读看板；不覆盖旧报告 |
| migrate | [--apply --authorization-file FILE --expected-plan-hash HASH] | 默认生成迁移计划；apply 必须绑定完整计划摘要 |
| verify-backup | --sha256 HASH | 校验内容哈希、JSON/schema 可读性，不修改库 |

搜索是本地词项加权余弦候选，不是神经语义检索。语义由使用此 Skill 的 AI 结合 catalog/history 推断，候选文件格式 `[{"id":"<UUID>","reason":"目标一致但用词不同，需人工核对"}]`。该候选保持不确定，不自动合并。无 embedding 模型、没有自动向 DeepSeek/OpenAI 发送私人材料，纯脚本无法理解跨词义概念。真正无名称线索时分页检索/阅读摘要，不能声称 lexical_only 已完成语义判断。

## apply 请求

必填 request_id、action、authorization、source。request_id 必须由客户端把会话/用户消息/意图绑定为一次逻辑操作；重试原样复用。相同 request_id 不同内容报 IDEMPOTENCY_CONFLICT，不追加第二条。该保护不是对“新 request_id 发送同一内容”的普遍去重；定位同一对象、逻辑消息 ID 由调用者负责。

authorization={basis:explicit_user|approved_scope, reference, quote}。approved_scope 另有 actions、record_ids 列表（`*` 代表明确授权的全库范围）。授权是审计契约，脚本不能证明提交者未伪造 quote，不是身份认证或操作系统访问控制。

source={ref,quote,evidence_status}。verified 表示有具体证据的主张，不因为本次写入成功就自动证明历史真实性。

| action | 额外字段 | 限制 |
|---|---|---|
| create | record:{name,kind?,aliases?,origin?,depth?,progress?,intent?}，text | 无成果不影响收录；精确候选存在需 confirmed_distinct=true + confirmation_quote；只有idea |
| append | record_id,text,changes?,details?,event_type?,occurred_at? | changes 仅origin/depth/progress/evidence_status；更新已有记录并追加事件 |
| alias | record_id,alias | 保留别名更新事件 |
| decision | record_id,text,changes:{intent,...} | 用户决定当前处理意向；AI建议须先获用户认可 |
| merge | source_id,target_id,confirmed_same_object:true,confirmation_quote | 两者同kind；保留原记录/事件，添加重定向及名称别名 |
| rollback | target_request_id,expected_revision | 只回滚最新 apply 操作，保留原事件及补偿事件；新记录标记rolled_back，不抹去证据；不回滚rollback，应追加明确纠正事件 |

将 AI 提议变为用户认可必须附 approval_quote。AI单方面建议/普通问答/证据不足对象默认未决定，可明确授权仅归档。append 不允许修改 ID、名称或直接塞关联项目 ID；项目新功能以原项目 ID 的事件表达。merge 是同对象确认，不是功能归入；不要合并 idea 与 project。

所有写入在同目录操作系统文件锁内重新读取最新版本、检查 request_id / revision、备份、校验完整新数据，再原子替换一次正本。record/event/receipt 在同一个提交内保存，不存在一半记录一半回执。每次修改前完整快照按 SHA256 保存到档案 `logs/.memory-sync-backups/`，返回 backup_sha256。

锁由进程退出自动释放，不凭文件年龄删锁。只覆盖使用本接口的协作客户端；人工编辑和绕过接口的旧脚本不受锁保护。损坏 JSON、冲突、超时或失败只返回失败；保留可核查备份。未声明网络盘、多机并发或突然断电耐久性。失败后的请求可用原 request_id 核对重试；若第一次已完成会返回 duplicate=true。

导入适合无分歧增量交接和同库往返；不支持离线分支双方编辑同一对象的自动三方合并，不支持不同库自动拼接。发生分歧先生成审阅方案；禁止直接恢复整份旧库覆盖随后写入。
