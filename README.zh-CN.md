<p align="right"><a href="README.md">English</a> | <a href="README.zh-CN.md"><b>简体中文</b></a></p>

# 🐾 Qoder CN 用量统计（`qcut`）

![Qoder CN](https://img.shields.io/badge/Qoder%20CN-reads%20local%20transcripts-blue?logo=robotframework&logoColor=white)
![Python](https://img.shields.io/badge/python-3.11%2B-blue?logo=python&logoColor=white)
![SQLite](https://img.shields.io/badge/storage-SQLite-003B57?logo=sqlite&logoColor=white)
![Textual](https://img.shields.io/badge/TUI-Textual-ff69b4?logo=terminal&logoColor=white)
![License: MIT](https://img.shields.io/badge/license-MIT-green)
![Platform: Linux](https://img.shields.io/badge/platform-Linux-lightgrey?logo=linux&logoColor=black)

> 🔭 看清你**实际**是怎么用 [Qoder CN](https://qoder.com.cn) 的——
> 每一个 🧰 工具、🎯 技能、🤖 子代理、🧩 插件、🔌 MCP 服务与 🔢 模型，
> 以及它们各自花掉的 💳 积分。

---

## 📖 目录

- [✨ 特性](#-特性)
- [🤔 为什么需要它](#-为什么需要它)
- [🏗️ 工作原理](#️-工作原理)
- [📊 八个标签页](#-八个标签页)
- [💳 积分与 token](#-积分与-token)
- [📋 环境要求](#-环境要求)
- [🚀 安装](#-安装)
- [🧭 用法](#-用法)
- [⚙️ 配置](#️-配置)
- [🔒 隐私](#-隐私)
- [🧪 测试](#-测试)
- [📁 仓库结构](#-仓库结构)
- [⚠️ 局限与路线图](#️-局限与路线图)
- [🧹 卸载](#-卸载)
- [🙏 致谢](#-致谢)
- [📄 许可](#-许可)

## ✨ 特性

- 🧰 **八个视图，一条命令** —— 仪表盘、工具、技能、代理、插件、MCP、Token、用量。
- 📜 **瞬间补齐全部历史** —— 把磁盘上已有会话全部回填：**173 个文件 / 362 MB 用时 5.2 秒**，索引 19 MB，之后只读新增字节（**热跑 0.10 秒**）。
- 🔑 **以名字为主键，任何东西都不会消失** —— 每个标签列出你**用过**的**以及**已安装的，仅以名字为键：改名、升级或删除的条目仍保留历史。
- 🪶 **被动且只读** —— 不装 hook、不写插件、不改动 Qoder 源码。
- 🗃️ **单个本地 SQLite 文件** —— 无网络、不上传、无遥测。
- 💳 **真实的积分与 token** —— Qoder 自有路由上报**积分**，BYOK 路由上报 **token**；两者都按原样存储，绝不互相换算。
- 🤖 **子代理也被计入** —— Qoder 为每次子代理运行写一个转录文件（`agent-aExplore-<hash>.jsonl`），qcut 把它们归回父会话，并用 `Agent` 工具的 `subagent_type` 驱动「代理」页。
- 🔌 **按 Qoder 的真实形态识别 MCP** —— 模型调用的是 `mcp_call` / `mcp_get` / `mcp_list`，目标写在参数里，因此服务与工具是从 `mcp__<server>__<tool>` 中拆出来的，而不是拿工具名去匹配。
- 📊 **按自然日分窗，且每个标签独立** —— 顶部范围选择器（今天 / 2 / 3 / 7 / 14 / 30 天 / 全部）**每个标签各存一份**，改一个不会动另一个。实体标签默认 **7 天**，仪表盘 / Token / 用量默认**今天**，插件页则始终是全部时间。窗口是完整的本地自然日（00:00 → 现在），绝不是「现在往前 N 小时」。
- 🔎 **两级下钻** —— 在工具、技能、代理、MCP 或 Token 页按 <kbd>Enter</kbd>，先看到该实体**在本标签窗口内**的汇总，下面是它的近期调用；再按 <kbd>Enter</kbd> 看到那次调用的**每一个已存字段**。
- 🔐 **只有元数据** —— 名称、耗时、状态、token、积分、项目路径。绝不涉及消息正文、提示词或工具参数取值。

## 🤔 为什么需要它

Qoder CN 其实**已经写下了**你需要的数据——只是从不**展示**它。它保存了：

- 📜 **会话转录**：`~/.qoder-cn/projects/<项目>/<会话>.jsonl`
  （每条模型响应带 `message.usage`，含 token 与**积分**；每次工具调用是一个带名字的 `tool_use` 块，并有一条配对的 `tool_result`）；

……而应用只给了单个会话的上下文占用条。跨会话的、按工具 / 技能 / 插件的拆分，以及「积分到底花在哪条路由上」，完全没有。这正是 `qcut` 填补的空缺。

## 🏗️ 工作原理

```text
   ~/.qoder-cn/                          qcut                          ~/.local/share/
 ┌──────────────────┐          ┌─────────────────────┐          ┌────────────────────────┐
 │ projects/        │          │  qcut-sync.py       │          │ qoder-cn-usage-        │
 │   **/*.jsonl  ───┼── 只读 ──▶  解析 + 归类       │          │   tracker/usage.db     │
 │                  │   读取   │  增量、幂等         │   写入   │                        │
 │ skills/          │          │                     ├─────────▶│  SQLite（tools ·       │
 │ plugins/         │          └─────────────────────┘          │  skills · agents ·     │
 │ mcp.json         │                    ▲                       │  plugins · mcp ·       │
 │ settings.json    │                    │                       │  models）              │
 └──────────────────┘          ┌─────────┴───────────┐           └───────────┬────────────┘
      原样不动                 │  qcut-tui.py (TUI)  │◀────── 查询 ──────────┘
                               │  qcut-stats.py (CLI)│
                               └─────────────────────┘
```

`qcut` 是**被动读取者**：它只解析 Qoder 已经写下的文件并建立索引。它不碰 Qoder 的文件、不安装 hook、不给宿主增加任何运行时开销。

## 📊 八个标签页

打开即是**仪表盘**：一眼看清关键数字。标签页上方有一条共享的顶栏承载范围选择器，**每个标签各存自己的范围**。**插件**页没有窗口——安装与否是既定事实，所以选择器在那里被禁用。窗口只改变**计数**，绝不丢行，因为每个标签都以名字为主键。

| | 标签 | 展示内容 |
|---|-----|---------|
| 🧭 | **仪表盘** | KPI 面板（请求数、积分、工具调用、失败、会话、项目）、含缓存命中率的 token 与积分面板、按天调用迷你条形图（带积分）、Top N 模型与 Top N 工具——全部取自仪表盘自己的窗口 |
| 🧰 | **工具** | 按名字统计的调用数、完成、失败、待定、平均耗时、最近使用 |
| 🎯 | **技能** | 按名字统计的技能调用，并在证据支持时给出所属插件 |
| 🤖 | **代理** | 按名字统计的子代理类型（`Explore`、`Plan`、`general-purpose`）、调用数、最近使用 |
| 🧩 | **插件** | 全部插件——已安装的**与**用过的——带版本号；全部时间 |
| 🔌 | **MCP** | MCP 服务与工具、调用数、平均耗时 |
| 🔢 | **Token** | 按模型统计请求数、输入 / 输出 / API 合计、缓存读取、**积分**，以及该路由是否上报了 token |
| 📊 | **用量** | 请求日志，最新在前：时间 · 模型 · 供应商 · 输入 · 输出 · API 合计 · 缓存读取 · 积分 · 停止原因 |

**每个标签都列出「用过」与「已安装」的并集，以名字为键，按调用数排序。** 装了但没用的插件仍以 `uses = 0` 出现；卸载过的仍保留历史。

表格绝不显示半个列：每张表保留能放下的**最长列前缀**，并在表格上方的说明行里点名被丢掉的列，因此窄终端付出的是列数，而不是可读性。

## 💳 积分与 token

这是读懂数字之前值得理解的一条 Qoder 特性，它是在本机**实测**而非假定：

| 路由 | 会上报 | 不上报 |
|---|---|---|
| Qoder 自有（`qfmodel`、`qmodel*`、`auto`） | **积分**（外加 `original_credits`、`billable`） | 任何 token 数——`input`/`output`/`cache` 送回来的是字面量 `0` |
| BYOK 路由（`qoder-custom-<id>/<model>`） | **token**（`input`、`output`、`cache read`） | 积分——`credits` 字段**根本不存在**，因此存成 NULL |

本机今天索引到的 29,628 条响应里，前一类 29,557 条，后一类 71 条，**没有一条同时具备两者**。

所以按积分计费的模型上报的是零 token，BYOK 模型上报的是没有积分——**两者都不是缺失数据，也都不由另一个推算而来。** 工具把这两种情况分开画，而不是抹平：真实数字照常打印，某条路由从未测出的零打印成 `—`，字段缺失打印成 `-`。Token 页的 `Tok reported` 列用的是同一个判定、给出是/否，因此这一列和那些破折号不可能各说一套。

两点算法说明：

- **API 合计 = 输入 + 输出。** 在 Qoder 的形态下 `input_tokens` 是**整个**提示词，`cache_read_tokens` 是它的**子集**——把缓存读取再加一次会让单个请求超出模型 40 万上下文，这正是验证该读法的方式。
- **缓存命中率 = 缓存读取 ÷ 输入。** 没有缓存数据时显示 `-`，绝不编造 `0%`。

## 📋 环境要求

| | |
|---|---|
| 🐧 系统 | Linux——在 Ubuntu 上开发与验证；其他发行版**未经验证** |
| 🐾 Qoder CN | 桌面应用；schema 是依据**本机转录**（agent 版本 `1.1.57`）验证的，而不是依据版本号 |
| 🐍 Python | 3.11 — 3.14，两端都实测通过（CI 就跑这两档）。无头命令只需标准库；TUI 需要 `textual` |
| 🖥️ `textual` | 仅交互式 TUI 需要（由 `install.sh` 安装） |

## 🚀 安装

```bash
cd qoder-cn-usage-tracker
./install.sh          # 创建带 textual 的 .venv，并链接 ~/.local/bin/qcut
qcut sync             # 从已有的 Qoder CN 转录建立索引
qcut                  # 启动 TUI
```

不想用 venv？无头报表用普通 `python3` 即可：

```bash
python3 scripts/qcut-sync.py
python3 scripts/qcut-stats.py stats
```

### 🔄 可选：不开 TUI 也能保持索引新鲜

[`systemd/`](systemd) 里有两个用户级单元。安装后每天执行一次 `qcut sync --quiet`：

```bash
mkdir -p ~/.config/systemd/user
cp systemd/qcut-sync.* ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now qcut-sync.timer
```

`Persistent=true` 会补跑关机期间错过的任务，唤醒时间带最多 15 分钟抖动，服务以 `Nice=10` 运行。

## 🧭 用法

```bash
qcut                     # 🖥️  交互式 TUI（八个标签）
qcut sync                # 🔄  增量索引新增转录
qcut sync --full         # ♻️  从零重建数据库
qcut stats               # 📊  各类别总览
qcut tools|skills|agents|plugins|mcp
qcut models              # 🔢  按模型的 token + 积分
qcut show tool Bash      # 🔍  某个实体的近期调用
qcut recent              # 🕒  最近的工具调用
qcut inventory           # 📦  已安装清单，用过 vs 未用
qcut export              # 💾  把每张表导出为 JSON
qcut health              # 🩺  数据库与数据源自检
qcut backup              # 💾  高风险操作前做快照
qcut restore             # ⏪  列出快照；`qcut restore NAME` 回滚
qcut format              # 🧾  本工具依赖的 Qoder 字段与路径
```

| 变量 | 默认值 | 用途 |
|---|---|---|
| `QCUT_DB` | `~/.local/share/qoder-cn-usage-tracker/usage.db` | 数据库位置 |
| `QCUT_QODER_DIR` | `~/.qoder-cn` | 转录来源目录 |

## ⚙️ 配置

每个开关都有它出厂时的取值，配置文件是可选的：在你不添加它之前，`qcut` 的行为与文档完全一致。

`~/.config/qcut/config.toml` —— 或用 `QCUT_CONFIG` 指向别处：

```toml
top_n = 5            # 仪表盘 Top 模型 / Top 工具的条数
log_limit = 100      # 用量页的请求行数
detail_limit = 200   # 历史页的行数
name_cap = 28        # 表格渲染单元格的最宽字符数
refresh_secs = 5     # TUI 重绘周期
sync_secs = 30       # TUI 自动同步周期
```

- **优先级为 环境 > 文件 > 默认值。** 每个键也可从 `QCUT_TOP_N`、`QCUT_LOG_LIMIT`、`QCUT_DETAIL_LIMIT`、`QCUT_NAME_CAP`、`QCUT_REFRESH_SECS`、`QCUT_SYNC_SECS` 读取。
- **文件损坏会中止 TUI**，并给出路径与原因，而不是悄悄用默认值启动。`qcut health` 给出同样的结论。
- **未知键会被拒绝**，取值有类型与范围校验。
- 由标准库 `tomllib` 解析，因此**不引入任何依赖**。该文件从不被写入，也没有任何设置被存进数据库。

## 🔒 隐私

`qcut` 只读取 Qoder 自身转录中的**结构化元数据**字段，存储计数、耗时、状态、token、积分与标识符。它**不**读取或存储提示词 / 回复正文、工具参数**取值**或文件内容。🔒 一切都不出本机。

其中三条承诺由检查而非善意保证——见 [`AGENTS.md`](AGENTS.md#invariants-and-how-to-check-them)。

## 🧪 测试

```bash
python3 -m unittest discover -s scripts/tests     # Ran 262 tests ... OK
```

测试套件是 `unittest`（`pytest` 也能发现它们）。其中两套需要安装 `textual`：`test_tui.py`，以及 `test_config.py`——后者的接线用例会把 TUI 模块加载起来，用以证明每个设置项真的到达渲染器。每个套件都在临时目录里自建一次性数据库**以及**一次性 Qoder 目录树——你真实的 `usage.db` 与 `~/.qoder-cn` 从不会被打开，仓库中也没有任何代码路径会访问网络。

| 套件 | 用例数 | 覆盖范围 |
|---|---:|---|
| `test_sync.py` | 43 | 转录解析、归类（工具 / 技能 / 代理 / MCP）、路由来源、清单、增量与全量重建、带时间戳的状态配对、未认领记录统计、schema 里**每一张表**的幂等性、崩溃安全 |
| `test_usage.py` | 53 | 自然日窗口、缓存命中率、全部查询辅助函数、「已安装 ∪ 用过」并集、token/积分那**同一条**上报判定及其 SQL↔Python 镜像、schema、最小版的 snapshot→list→restore 往返、尚无数据库的全新安装，以及无头报表的措辞 |
| `test_stats.py` | 32 | 无头 CLI 的端到端：每个子命令都真的调用 `main([...])`，`--db` 与 `--limit`、只读连接、索引过期时的拒绝并指出修法、`export` / `inventory` / `recent` / `show` / `health`、`backup`/`restore` 这一对，以及入口脚本绝不碰它没被给过的路径 |
| `test_snapshots.py` | 13 | 破坏性步骤的回退之路：快照在改动**之前**取、是在线备份而非把还在 `-wal` 里的主文件按字节拷贝、restore 保留它覆盖掉的状态，以及回收只删自己生成的文件 |
| `test_config.py` | 16 | 设置层：默认值、文件、环境优先级与边界、损坏文件指名路径、`qcut health` 打印的键 |
| `test_privacy.py` | 9 | 生产脚本的导入白名单、证明源目录未被改写的指纹、跨所有文本列的哨兵串扫描，以及正向对照 |
| `test_tui.py` | 41 | 用 Textual 自带 `run_test` 验证标签接线、每标签独立范围、插件页无窗口、列计划、空状态、下钻、两根度量轴上 `—`/`-`/数字的三态区分、标签名与画布底色这两条外观约定，以及**真正显示在屏幕上**（而非仅仅存有文本）的状态行 |
| `test_dispatcher.py` | 9 | `bin/qcut` 路由、参数透传，以及它之所以用 bash 的那些失败模式 |
| `test_format_registry.py` | 10 | 用 AST 把格式登记表与它旁边的解析器互为镜像：读到的字段必须登记、登记的字段必须仍在读、分发链与登记表声称认领同一组记录类型，且分隔符 / 路径段 / 工具名都不许在调用点手打 |
| `test_maintenance_docs.py` | 10 | 生成的格式文档等于 `qcut format` 今天打印的内容、每个维护资产都能从路由文档抵达、每条链接都能解析，以及 CI 矩阵钉住的正是 `pyproject.toml` 声明的下限 |
| `test_readme_counts.py` | 6 | 两份 README 里每一个用例数字——每行、每个总数——都等于从套件语法树上读下来的数量；盘上有套件却没有对应行，与行指向已删除的文件，同样判红 |
| `test_readme_bilingual.py` | 7 | 两份 README 是同一份文档的两种语言：标题表情符号序列与其顺序必须一致，因此任何一侧单独增 / 删 / 移动小节都会判红；每条页内链接都指向真实标题 |
| `test_record_coverage.py` | 13 | 改动生产脚本的提交必须在 `CHANGELOG.md` 里留下同种类的条目；窗口是从 `git log` 枚举而非手打的，且窗口里无可覆盖内容时判定为跳过而非通过 |

GitHub Actions 在每次 push 与 pull request 上跑同一条 discovery 命令，矩阵是 **Python 3.11 与 3.14**——分别是 `pyproject.toml` 声明的下限，与本机实测通过的最高版本。两端都要覆盖，是因为本地 venv 是 3.13：中间版本哪一端都说明不了。

## 📁 仓库结构

| 路径 | 说明 |
|---|---|
| `bin/qcut` | bash 入口。故意用 bash：当 TUI 的 venv 缺失或损坏时它仍须给出有用报错，因此不能依赖 Python |
| `install.sh` | 创建 venv（有 `uv` 时优先用它）并链接 `~/.local/bin/qcut` |
| `scripts/qcut_db.py` | SQLite schema 与各入口共享的查询辅助 |
| `scripts/qcut-sync.py` | 转录解析器与增量索引器（`--full`、`--quiet`） |
| `scripts/qcut-stats.py` | 无头报表 |
| `scripts/qcut-tui.py` | 八标签 Textual UI |
| `scripts/tests/` | 上述 262 个用例 |
| `docs/maintenance/` | 自动生成的格式面与体积性能基线 |
| `systemd/` | 可选的每日同步 service + timer |
| `requirements.txt` | `textual>=8.2,<9` —— 仅 TUI 需要；其余全部是标准库 |

两条路径都可在沙箱安装中覆盖：启动器用 `QCUT_SCRIPTS` 与 `QCUT_VENV`，数据用 `QCUT_DB` 与 `QCUT_QODER_DIR`。

## ⚠️ 局限与路线图

- 🔌 **MCP 用量天然稀疏**，直到你真的调用 MCP 工具；本机是 2 个服务共 10 次调用。稀疏的 MCP 页是诚实的，不是坏了。
- 🧩 **插件归属基于证据。** 名为 `plugin:skill` 的技能（或某插件安装的技能）会记到该插件名下；插件自带的代理与命令会从其安装目录列出，但**不**从转录中归属，因为 Qoder 并未在那里记录这种归属关系。
- 💳 **Token 合计只覆盖 BYOK 流量**——在以自有路由为主的机器上如此，见 [积分与 token](#-积分与-token)。这是 Qoder 写什么的问题，不是读取器的缺口。
- 📅 **尚无自定义日期区间。** 范围选择器提供上述七个窗口；任意起止时间的日期选择器是最值得补上的下一步。
- 🔄 同步是手动的（或用可选的 systemd 定时器）；没有实时 hook。

## 🧹 卸载

`qcut` 只碰仓库之外的三处，全部删掉后不留痕迹：

```bash
systemctl --user disable --now qcut-sync.timer 2>/dev/null          # 仅在安装过时
rm -f ~/.config/systemd/user/qcut-sync.service ~/.config/systemd/user/qcut-sync.timer
systemctl --user daemon-reload
rm -f ~/.local/bin/qcut                                            # install.sh 建的符号链接
rm -rf ~/.local/share/qoder-cn-usage-tracker                       # SQLite 索引
```

再删掉仓库目录（含 `.venv`）即完成。Qoder 自己的文件始终只被读取，那边无需恢复。🗑️

## 🙏 致谢

灵感来自 [`codebuddy-usage-tracker`](https://github.com/SHADE-glitch/codebuddy-usage-tracker)——它跟踪 CodeBuddy Code 的同类维度。架构一致（读取宿主已经写下的东西），但 schema 是 Qoder 自己的：用积分而非供应商上报合计，用 `Skill`/`Agent` 工具调用而非标记解析，MCP 写在参数里而非工具名里。

## 📄 许可

MIT —— 见 [LICENSE](LICENSE)。© 2026 SHADE-glitch
