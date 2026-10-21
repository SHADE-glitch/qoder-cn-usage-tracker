# MAINTENANCE.zh-CN.md — qoder-cn-usage-tracker

流程文件。这是一份**路由表**：长文维护资产放在
[`docs/maintenance/README.md`](docs/maintenance/README.md) 下，仓库内的工作规则放在
[`AGENTS.md`](AGENTS.md)。不要在这里复制它们的表格——被复制的那份事实才是会漂移的东西。

唯一真正要紧的维护风险：**Qoder 改了转录格式**。字段改名会被读成 `NULL`，记录类型改名会静默地
无人认领，面板清空，`qcut sync` 照样打印 "done"，CI 照样绿。本工具依赖的格式面登记在
[`docs/maintenance/qoder-format.md`](docs/maintenance/qoder-format.md)，它由代码生成——用
`qcut format --write` 重新生成，并且把登记表和处理函数**放在同一个 commit 里**改。

第二个风险是这个工具特有的，而且很容易搞错：token 与积分**不是互补**关系。只要 CLI 自己的环境里
有 `QODERCN_EXPOSE_TOKEN_USAGE=1`，托管路由的一次响应就会同时带来真实 token **和**积分；没有这个
变量时，同一条路由只有积分、四个 token 字段全是字面量 `0`，而 qcut 无法修复这段历史。覆盖率是
**上报了 token 的请求**所占的比例——绝不是 token 体积的占比，也绝不从积分估算。读法（`—` / `-` /
数字，以及它们背后那条唯一判定）写在 `AGENTS.md` § 硬性规则；实测分布写在
[`docs/maintenance/baseline.md`](docs/maintenance/baseline.md)。**永远不要由一根轴推算另一根。**

## Qoder CN CLI 升级之后

1. 同步进一个一次性库（`QCUT_DB=/tmp/qcut-check.db qcut sync --quiet`）并读 `qcut health`。
   `unparsed` 里出现点名某个记录类型的新原因，说明格式动了：把它认领为 no-op 或正式处理，并在
   同一次改动里用 `qcut format --write` 重新生成
   [`qoder-format.md`](docs/maintenance/qoder-format.md)。
2. 在**隔离的 shell** 里复验 token 捕获：设置 `QODERCN_EXPOSE_TOKEN_USAGE=1`，发起一次托管调用，
   把那一次会话同步进临时库，再读 `qcut stats` / `qcut models`。托管调用必须上报非零的输入 /
   输出 token（积分也可能同时存在），覆盖率必须上升。没有这个变量时同一次调用会存 0 token——
   这个差异就是变量仍然生效的证明。
3. 绝不对实时索引做探针，绝不写入 `~/.qoder-cn`。

## 验证档位
- **L0** —— `python3 -m unittest discover -s scripts/tests`（临时数据库，不碰主机）。有两套需要
  `textual`：`test_tui.py` 与 `test_config.py`，所以请用 `.venv/bin/python` 跑。
- **L1** —— 仓库外的一次性库，喂真实转录：
  `QCUT_DB=/tmp/qcut-check.db python3 scripts/qcut-sync.py`。解析器的改动在这里证明；同步报告里
  **每张表**的行数，就是让你没动过的那张表的退化保持可见的办法。
- **L2** —— 真实的 `~/.qoder-cn` 转录与真实的
  `~/.local/share/qoder-cn-usage-tracker/usage.db`。需要机主点头；破坏性步骤先快照
  （`qcut sync --full` 会自动做）。回收发生在 `db.snapshot()` 内部：任何一条快照路径都只留最新
  的五份，同秒的第二次快照改用 `-1`/`-2` 后缀而不是覆盖它本来要保住的那份，而索引里没有内容时干脆不快照。

## 产出这些数字的命令
```bash
find ~/.qoder-cn/projects -name '*.jsonl' | wc -l      # 源文件数
QCUT_DB=/tmp/qcut-check.db qcut sync --quiet           # 重建索引；打印每表行数
QCUT_DB=/tmp/qcut-check.db qcut health                 # 设置、各表计数、未认领原因
QCUT_DB=/tmp/qcut-check.db qcut models                 # 按路由的积分、token 与覆盖率
QCUT_DB=/tmp/qcut-check.db qcut env                    # 捕获开关状态与索引覆盖率
```

面板空了时第一个要读的是 `qcut health`：`unparsed` 里出现点名某个记录类型的原因，说明格式动了；
某张表归零而邻居都在涨，说明字段被改名了。

## CI
`.github/workflows/ci.yml` 在 Python 3.11 / 3.14 矩阵上跑离线层——即声明的下限，与本机实测通过的
最高版本。它不是你开发用的解释器（`install.sh` 建的是 3.13 venv），一次绿的运行对它没跑过的版本
什么都不说明。**跑批机的时钟是 UTC**，你 shell 的大多不是：日期敏感的失败请用
`TZ=UTC python3 -m unittest discover -s scripts/tests` 复现，并且记住 CI 只看得到**已推送**的提交。

## 资产
| 文档 | 什么时候读 |
|---|---|
| [`qoder-format.md`](docs/maintenance/qoder-format.md) | Qoder 发了新版本，或某个面板不再显示它以前显示的东西 |
| [`compatibility.md`](docs/maintenance/compatibility.md) | 要决定声明支持什么，或某个版本号需要更新时 |
| [`baseline.md`](docs/maintenance/baseline.md) | 要重述某个体积、耗时、行数，或路由分布时 |

两份文档里的数字都是**一台机器、某一天**量出来的。转录目录树是活的，就在你测量它的这次会话里继续
增长——请重新测量，而不是把一个数字往下抄。
