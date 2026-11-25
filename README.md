# 行业术语入库后的语料和考核分组

面向“校企共建中文数据实训”的 Python 项目：教研人员从持续加入的**术语候选、工单片段、
试题修订**制作交付给院校的课堂资料包与学期问卷包，全程保留原字面位置、区分同形异义、
管理“同一考查证据”的派生关系，并在发现泄漏时优先保住已交付内容。

仅依赖 Python 标准库（>= 3.10）。

## 业务规则与系统对应

| 业务要求 | 实现位置 | 行为 |
| --- | --- | --- |
| 工单含复合词、单位、标点变体，判断词界/角色/语境并保留原字面位置 | `corpuskit/linguistics.py` | 每个 Token 携带半开区间 `Span`，`text == raw_text[start:end]`；单位与标点只写归一形 `norm`，永不改写原文；复合词保留内部词界 |
| 两处词形相同语义不同（如“主键回填”技能 vs “主键回填表”表名）分别组织 | `Term/Sense` + 语境消歧 | 词目下挂多个词义，按业务语境线索打分消歧，标注引用 `词目ID#词义ID` |
| 同义改写可认出源题证据已进入训练材料 | `corpuskit/evidence.py` | bigram 相似度产生机器建议，人工 `confirm` 后才成立 |
| 派生练习拼接旧片段、新题只复用观察量换问法 | 证据边 `split/paraphrase/derive` 与 `reuse_obs` | 前者 `same_evidence=True` 参与聚类；后者明确 `same_evidence=False`，只共用名词/数值可分开 |
| 多跳派生也不能让同一证据横跨课堂与未开考问卷 | 并查集传递闭包 + `corpuskit/leakage.py` | 确认边求连通块，任一成员进课堂包，整簇对问卷泄漏；屏障双向生效 |
| 问卷有必考能力×语境，泄漏时先保课堂再找另一组无泄漏考题 | `corpuskit/planning.py` | 回溯集合覆盖；有替代题则给出最小选题，无替代则显式报出哪个能力×语境无法同时交付，不削减必考项、不撤课堂包 |
| 撤回词义修订后重判未发内容，已交付问卷按当时口径查阅 | `corpuskit/revisions.py`、`corpuskit/packages.py` | 修订走 提案→生效→撤回 生命周期；撤回只重判未交付片段并使旧规划 stale；交付包冻结词目、标注、证据簇快照，永久可查 |
| 两家院校分别交付 | 实体 `colleges` 归属 + 屏障按院校过滤 | 院校 A 的课堂包不会阻挡院校 B，院校专属题不跨校使用 |
| 全过程可追溯 | `Store.events.log` | 仅追加 JSON Lines 审计日志，所有写盘走临时文件原子替换 |

## 目录结构

```
corpuskit/
  models.py       # dataclass 领域模型（词目/词义/片段/标注/证据边/试题/必考单元/规划/交付包/修订）
  store.py        # JSON 集合存储、ID 发放、原子写入、仅追加事件日志
  linguistics.py  # 词界、角色、语境消歧、单位/标点归一、复合词内部边界
  evidence.py     # 并查集、多跳证据聚类、机器建议、评审决定
  leakage.py      # 双向泄漏屏障、院校隔离、规划陈旧判定
  planning.py     # 必考能力×语境的回溯集合覆盖与显式冲突
  revisions.py    # 词义修订生命周期、撤回与未交付内容重判
  packages.py     # 课堂/问卷交付包冻结快照与历史查阅
  ingest.py       # 种子引导、持续外部导入
  cli.py          # 命令行入口
scripts/demo.py   # 端到端演练（含 48 条内置断言）
tests/            # 44 条 unittest 回归
data/corpus.json  # 合成种子记录（原有，未改动其文字）
bin/corpuskit     # python -I 下可用的命令行包装
```

## 运行检查

```bash
# 标准库即可；-t . 让项目根进入 sys.path，配合 -I 隔离运行
python3 -I -m unittest discover -s tests -t . -v

# 端到端演练（临时工作区，结束自动清理；48 条断言）
python3 -I scripts/demo.py --check
```

## 常用命令（在项目根目录）

```bash
CK="python3 -I bin/corpuskit --store ./corpus_store"

$CK init --seed data/corpus.json           # 种子引导
$CK term-list                              # 词目与词义
$CK sense-index 主键回填 主键回填表 --context 工单 --context 工具菜单
$CK fragment-show f-1                      # 带偏移/角色/词义的标注
$CK edge-suggest                           # 机器证据建议（仍需人工 confirm）
$CK evidence-clusters                      # 同一考查证据聚类
$CK leakage --college college-a            # 问卷泄漏报告
$CK plan-make --college college-a          # 问卷规划（成功选题或显式冲突）
$CK revision-propose --term t-2 --kind add_variant --variant 补键汇总表 --activate
$CK revision-revert r-1 --reviewer 教研组长
$CK package-questionnaire --college college-a --plan p-1
$CK package-show d-2                       # 按交付时词义与分组查阅
$CK events                                 # 审计日志
```

所有列表/报告命令在全局或子命令后加 `--json` 可输出机器可读结果。

## 外部数据安全约定

新加入的术语候选与工单文件是**不受信数据**：

1. 每个外部文件放在独立的新空目录，项目代码不与数据混放；
2. 用绝对路径传给 `fragment-import`，读取进程一律 `python3 -I`
   （隔离 `PYTHONPATH`、用户 site 包与当前目录隐式导入，避免数据目录内被植入的
   `json.py` 之类模块被 `import` 加载）；
3. 导入只做 JSON 解析与规则标注，不执行数据中的任何代码。

## 数据落盘

工作区目录（默认 `corpus_store/`）保存 `*.json` 集合与 `events.log`、`counters.json`，
可直接整目录备份或随交付包归档；交付包内的快照使其在后续词义变化后仍可按当时口径复核。
