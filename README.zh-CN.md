<p align="right"><a href="README.md">English</a> | <a href="README.zh-CN.md"><b>简体中文</b></a></p>

# 🐾 Qoder CN CLI 用量统计（`qcut`）

![Qoder CN CLI](https://img.shields.io/badge/Qoder%20CN%20CLI-reads%20local%20transcripts-blue?logo=robotframework&logoColor=white)
![Python](https://img.shields.io/badge/python-3.11%2B-blue?logo=python&logoColor=white)
![SQLite](https://img.shields.io/badge/storage-SQLite-003B57?logo=sqlite&logoColor=white)
![Textual](https://img.shields.io/badge/TUI-Textual-ff69b4?logo=terminal&logoColor=white)
![License: MIT](https://img.shields.io/badge/license-MIT-green)
![Platform: Linux](https://img.shields.io/badge/platform-Linux-lightgrey?logo=linux&logoColor=black)

**是什么** —— 一个针对 [Qoder CN CLI](https://qoder.com.cn) 的被动追踪器，**模仿 `cbut`**（同族的 [codebuddy-usage-tracker](https://github.com/SHADE-glitch/codebuddy-usage-tracker)）：八标签页 TUI 与无头报告，覆盖你用到的每一个工具、技能、子代理、插件、MCP 服务与模型，以及它们各自花掉的 token 与积分。它只读 Qoder 的文件，只写自己的本地索引。

**解决什么** —— Qoder CN CLI 每个会话、每次子代理运行各写一个转录文件；`qcut` 把它们归并进一个本地 SQLite 文件，让用量、token 与积分一目了然——并分清「真实的零」与「路由根本没上报 token」。

**怎么跑** —— `./install.sh`，然后 `qcut sync`。

**去哪看** —— 见 [AGENTS.md](AGENTS.md) 与 [MAINTENANCE.md](MAINTENANCE.md)。

> 🔭 看清你**实际**是怎么用 [Qoder CN CLI](https://qoder.com.cn) 的——
> 每一个 🧰 工具、🎯 技能、🤖 子代理、🧩 插件、🔌 MCP 服务与 🔢 模型，
> 以及它们各自花掉的 💳 积分与 🔑 token。

---

## 📖 目录

- [✨ 特性](#-特性)
- [🤔 为什么需要它](#-为什么需要它)
- [🧬 与 cbut 的关系](#-与-cbut-的关系)
- [🏗️ 工作原理](#️-工作原理)
- [📊 八个标签页](#-八个标签页)
- [⌨️ 键盘](#️-键盘)
- [🔑 Token 捕获](#-token-捕获)
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
- [🤝 参与贡献](#-参与贡献)
- [🙏 致谢](#-致谢)
- [📄 许可](#-许可)

## ✨ 特性

- 🧰 **八个视图，一条命令** —— 仪表盘、工具、技能、代理、插件、MCP、Token、用量。
- 📜 **瞬间补齐全部历史** —— 把磁盘上已有会话全部回填：**489 个文件 / 522 MB 用时 6.8 秒**，索引 25 MB，之后只读新增字节（**热跑 0.10 秒**）。
- 🔑 **以名字为主键，历史不会消失** —— 标签默认只显示**本窗口内用过**的条目，与 Token 页一贯的边界一致；按 <kbd>a</kbd> 切换到「已安装 ∪ 用过」。两种视图都以名字为键，改名、升级或删除的条目仍保留历史。
- 🪶 **对 Qoder 保持被动** —— 只读 Qoder 的转录，只写自己的本地索引：不装 hook、不写插件、不改动 Qoder 源码。
- 🗃️ **单个本地 SQLite 文件** —— 无网络、不上传、无遥测。
- 💳 **真实的积分与 token——按上报原样存储** —— 每个值都按 Qoder 写入的原样保存。开启[token 开关](#-token-捕获)后，托管路由的一行可以同时带 token 与积分；历史托管行带积分、四个 token 字段都是字面量 `0`；BYOK 行带 token、没有 `credits` 字段。绝不估算、换算或回填：`—` 标记路由层面的零，`-` 标记缺失值。
- 🤖 **子代理运行就是一本成本账** —— Qoder 为每次子代理运行写一个转录文件（`agent-aExplore-<hash>.jsonl`）；qcut 把它们归回父会话，并只用 Qoder 自己的 `parent_tool_use_id` 把每条响应关联到发起它的 run，绝不按时间或类型猜测。代理页按类型显示 run 数、成功/失败、关联请求、**用量合计**、积分与峰值上下文；<kbd>Enter</kbd> 进入该类型的 run，再按 <kbd>Enter</kbd> 打开该 run 发起的响应。没有关联响应的 run 显示 `0` 请求与 `-` token，而不是编出来的成本。
- 📁 **项目与上下文账本** —— 仪表盘的项目板走「项目 → 会话 → 请求 → 调用」，带请求数、用量合计、积分、峰值上下文与最近使用；项目详情页再补上会话与压缩明细。`qcut projects` 打印同一块项目板，`qcut projects <路径>` 下钻到单个项目。压缩丢弃的 token **并列**显示在 token 合计旁，绝不加进合计。
- 🔌 **按 Qoder 的真实形态识别 MCP** —— 模型调用的是 `mcp_call` / `mcp_get` / `mcp_list`，目标写在参数里，因此服务与工具是从 `mcp__<server>__<tool>` 中拆出来的。较旧的转录直接拿 `mcp__<server>__<tool>` 当工具名的，也能识别；没有服务端的 admin 操作显示为 `(admin)`。
- 📊 **按自然日分窗，且每个标签独立** —— 顶部范围选择器（今天 / 2 / 3 / 7 / 14 / 30 天 / 全部）**每个标签各存一份**，改一个不会动另一个。实体标签默认 **7 天**，仪表盘 / Token / 用量默认**今天**，插件页则始终是全部时间。窗口是完整的本地自然日（00:00 → 现在），绝不是「现在往前 N 小时」。**Custom…** 选项与 📅 控件会打开 cbut 的日期区间选择器——六个宽敞的预设按钮、可编辑的起止日期与时间、月历与小时格——选出的区间只属于当前标签。选择器为 79×30，恰好放进 80×30 终端；更矮的终端会滚动。
- 🔎 **一路下钻到底** —— 在工具、技能、代理、MCP 或 Token 页按 <kbd>Enter</kbd>，先看到该实体**在本标签窗口内**的汇总，下面是它的近期调用；代理页继续进入 run 及其响应，仪表盘项目板继续进入会话与请求。Token 行会保留**供应商**，用量行直接打开**那一条请求**而不是整个模型的列表。再按 <kbd>Enter</kbd> 看到那次调用的**每一个已存字段**，包括它的 token 状态。
- 🔐 **只有元数据** —— 名称、耗时、状态、token、积分、项目路径。绝不涉及消息正文、提示词或工具参数取值。

## 🤔 为什么需要它

Qoder CN 的 CLI 其实**已经写下了**你需要的数据——只是从不**展示**它。它保存了：

- 📜 **会话转录**：`~/.qoder-cn/projects/<项目>/<会话>.jsonl`
  （每条模型响应带 `message.usage`，含 token 与**积分**；每次工具调用是一个带名字的 `tool_use` 块，并有一条配对的 `tool_result`）；

……而 CLI 只给了单个会话的上下文占用条。跨会话的、按工具 / 技能 / 插件的拆分，以及「积分到底花在哪条路由上」，完全没有；也分不清某个零是路由从未测量，还是真的为零。这正是 `qcut` 填补的空缺。目标是 **CLI 自己的转录目录**；Qoder IDE / 桌面应用不在范围内，也不会被索引。

## 🧬 与 cbut 的关系

`qcut` 是对 **`cbut`（[`codebuddy-usage-tracker`](https://github.com/SHADE-glitch/codebuddy-usage-tracker)）的刻意模仿**——后者是跟踪 CodeBuddy Code 的同族项目；`tcut` 是三兄弟中的另一个。同族形态来自 `cbut`：被动读取宿主已经写下的文件、同样顺序的八个标签槽、每标签独立的自然日窗口、以名字为键的「已安装 ∪ 用过」并集，以及两级下钻。

它是模仿，**不是移植，也不以完全对齐为目标**。对象是 **Qoder CN CLI**，所以一个标签能显示什么，由 Qoder 的转录决定。`cbut` 的某个类别在 Qoder 里有字段支撑，qcut 就保留；没有的，就明确不做并说明原因，而不是编一个占位——单次请求耗时、HTTP 状态和金额在 Qoder 里都没有字段。反过来，`cbut` 没有而 Qoder 有的事实被保留并作为一等公民：与 token 并列的积分、区分「路由没上报 token」的 `—`/`-`、与 `API Total` 并列的 `Usage Total`，以及缓存读取 / 未命中 / 写入。「Qoder 到底记录了什么」才是问题；把界面做得和 cbut 一模一样不是。

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

打开即是**仪表盘**：一眼看清关键数字。标签页上方有一条共享的顶栏承载范围选择器与 📅 控件，**每个标签各存自己的范围**。**插件**页没有窗口——安装与否是既定事实，所以两个控件在那里都隐藏。窗口改变**计数**与显示哪些实体行，但绝不删除历史。选择 **Custom…** 或按 📅 会打开日期区间选择器，其 Confirm 只把选中的起止时间应用到当前标签。

| | 标签 | 展示内容 |
|---|-----|---------|
| 🧭 | **仪表盘** | 用量面板（请求数、积分、会话、项目、内置工具调用、技能、Agent、MCP、插件）、Token 面板（用量合计 · 输入 · 输出 · API 合计 · 积分 · 缓存命中率）、缓存面板、运行状态（完成 / 失败 / 待定 / 平均工具耗时 / 会话 / 项目 / 后台与取消 / 峰值上下文 / 压缩税）、按天调用迷你条形图、Top 模型、Top 工具，以及**项目板**（请求 · 用量合计 · 积分 · 峰值上下文 · 最近使用）——全部取自仪表盘自己的窗口；项目板随内容长高，并吃掉上面几行剩下的高度，让仪表盘铺到状态行，项目多到放不下时窗格滚动 |
| 🧰 | **工具** | 按名字统计的内置工具调用：调用数 · 成功 · 失败 · 待定 · 平均耗时 · 最近使用 |
| 🎯 | **技能** | 按名字统计的技能调用：调用数 · 成功 · 失败 · 所属插件 · 最近使用 |
| 🤖 | **代理** | 子代理类型：**run 数 · 成功 · 失败 · 关联请求 · 用量合计 · 积分 · 峰值上下文 · 最近使用**；没有关联响应的 run 显示 `0` 请求与 `-` token |
| 🧩 | **插件** | 全部插件——已安装的**与**用过的——带来源、版本、归因调用数与已安装资源；全部时间 |
| 🔌 | **MCP** | MCP 目标：服务 · 工具 · 调用数 · 成功 · 失败 · 平均耗时 · 最近使用；admin 操作显示 `(admin)` |
| 🔢 | **Token** | 按模型的**供应商**（Qoder 自有路由为 `Built-in`，BYOK 渠道显示渠道名）、**用量合计 · 积分 · 请求数**、输入 / 输出 / API 合计、缓存读取 / 未命中 / 写入、缓存命中率、最近使用 |
| 📊 | **用量** | 请求日志，最新在前：时间 · 模型 · **供应商** · **用量合计 · 积分** · 输入 / 输出 · API 合计 · 缓存命中 / 未命中 / 写入 · 缓存命中率。<kbd>Enter</kbd> 直接打开该次调用 |

`供应商` 只有一列，不再拆成「家族 + 名称」：Qoder 自有路由显示 `Built-in`，BYOK/第三方调用显示实际提供服务的渠道。

**实体标签默认只显示本窗口内用过的行——与 Token 页一贯的边界相同。** 按 <kbd>a</kbd> 切换到「已安装 ∪ 用过」全集（说明行会指出隐藏了多少行）；插件页则始终是完整安装清单。Token 与用量默认按**用量合计、再按积分**排序，Top 工具按调用数排序；<kbd>s</kbd> 可切换当前标签自己的排序，说明行会写明当前顺序。

表格绝不显示半个列：每张表保留能放下的**最长列前缀**，并在表格上方的说明行里点名被丢掉的列，因此窄终端付出的是列数，而不是可读性。

## ⌨️ 键盘

标签栏与页脚就是全部按键面，同一套按键也用于姊妹工具 `ocut` 与 `cbut`。

| 按键 | 动作 |
|---|---|
| `q` | 退出 |
| `r` | 立即同步——执行一次增量同步，然后重载界面 |
| `s` | 循环当前标签的排序；表格上方的说明行会写出当前顺序 |
| `a` | 在工具 / 技能 / 代理 / MCP 页显示「已安装 ∪ 用过」全集 |
| `/` | 过滤当前标签；有过滤时过滤栏保持可见，`enter` 把焦点还给表格，`escape` 清除 |
| `d` | 选择当前标签的时间窗口 |
| `tab` / `shift+tab` | 下一个 / 上一个标签 |
| `enter` / 双击 | 打开所选行的详情页 |
| `escape` / `q` | 清除当前过滤，或在推入的详情页返回 |

TUI 打开时只执行**一次**增量同步，之后静止：没有定时器重绘页面，也没有后台同步写索引。新数据只会由 `r` 或重新打开 TUI 带来。每次编辑过滤都会把列表定位到第一个匹配项，每张表上方的说明行同时写出当前排序与匹配数。用量页的过滤与排序交给 SQL 在整个窗口上完成——不是只排当前这一页——因此 `log_limit` 只截断输出，不会藏掉匹配项。

推入的详情页独占按键：应用级快捷键在那里全部停用，只保留该页自己的 `escape`/`q` 返回。

## 🔑 Token 捕获

Qoder CN CLI 在**自己启动时**读取一个环境变量：

```bash
export QODERCN_EXPOSE_TOKEN_USAGE=1
```

- **必须在 CLI 启动前设置。** 这个值由 Qoder CN CLI 进程读取，不是 qcut：把这一行写进 shell 配置并开一个新终端；在运行 `qcut` 的 shell 里设置它没有任何作用。
- **未设置** —— 托管响应会写入真实积分，以及四个 token 字段的字面量 `0`。那个零不是测量值。
- **已设置** —— 托管响应会写入真实的输入 / 输出 / 缓存读取 / 缓存写入 token；同一行里也可能带积分。BYOK 路由两种情况都上报 token，且从不带 `credits` 字段。
- **历史调用无法修复。** 转录里的零已经写死、永不重写。这些行保留真实积分，token 单元格显示 `—`——绝不是测量出来的 `0`。没有覆盖率百分比或捕获分界账本：索引只展示每一行实际上报了什么。当一个窗口里带积分的行完全没有 token 时，用量页与 Token 页会在读者看到破折号的地方写明：`token capture off — set QODERCN_EXPOSE_TOKEN_USAGE=1 before starting Qoder`。

qcut 绝不编辑 `~/.qoder-cn`、你的 shell 配置或 CLI 的环境。`qcut env` 只是只读建议：它点名变量、报告当前状态、打印要添加的那一行。数据库不存在时它也能工作，并且不会创建数据库。任何 token 数字都不会由积分、上下文占比或其他东西推算。

## 💳 积分与 token

这是读懂数字之前值得理解的一条 Qoder 特性，它是在本机**实测**而非假定：

| 路由 | token | 积分 |
|---|---|---|
| 托管路由，未开启捕获（历史） | 每个 token 字段都是字面量 `0` | 真实积分（外加 `original_credits`、`billable`） |
| 托管路由，已开启捕获 | 真实的输入 / 输出 / 缓存读取 / 缓存写入 | 同一行里也可能存在 |
| BYOK 路由（`qoder-custom-<id>/<model>`） | 真实的输入 / 输出 / 缓存读取 | `credits` 字段**根本不存在**，存成 NULL |

token 与积分**不是互补关系**。只要 Qoder 同时上报两者，同一行就可以同时拥有；上表描述的是每条路由写什么，不是同一个度量的两半。qcut 按上报原样存储，并把差异画出来：

- 数字 = 真实测量值；
- `—` = 该行（或这类行的合计）完全没有上报 token——是路由层面的零，绝不表示「没有使用」；
- `-` = 字段缺失，或本窗口内没有内容。

因此同一张表可以同时容纳两类行：测量值保持数字，路由层面的零在旁显示为 `—`。用量合计是把缓存命中（已包含在输入里）再加一次的展示合计，与 API 合计并列，而不是取代它。

**不估算、不换算：** token 绝不由积分或上下文占比推算，积分也绝不由 token 推算。历史可以查看，但不能修补。

两点算法说明：

- **API 合计 = 输入 + 输出。** 在 Qoder 的形态下 `input_tokens` 是**整个**提示词，`cache_read_tokens` 是它的**子集**——把缓存读取再加一次会让单个请求超出模型 40 万上下文，这正是验证该读法的方式。
- **缓存命中率 = 缓存读取 ÷（读取 + 未命中 + 写入）**，即三者划分的可缓存输入。没有缓存数据时显示 `-`，绝不编造 `0%`。

## 📋 环境要求

| | |
|---|---|
| 🐧 系统 | Linux——在 Ubuntu 上开发与验证；其他发行版**未经验证** |
| 🐾 Qoder CN | **CLI**，依据版本 `1.1.75`（2026-10-21）与本机转录验证（转录里仍有 `1.1.57` 的会话）；schema 读自文件本身，不依赖版本号 |
| 🐍 Python | 3.11 — 3.14，两端都实测通过（CI 就跑这两档）。无头命令只需标准库；TUI 需要 `textual` |
| 🖥️ `textual` | 仅交互式 TUI 需要（由 `install.sh` 安装） |

## 🚀 安装

```bash
cd qoder-cn-usage-tracker
./install.sh          # 创建带 textual 的 .venv，并链接 ~/.local/bin/qcut
qcut sync             # 从已有的 Qoder CN 转录建立索引
qcut                  # 启动 TUI
```

> **升级已有索引？** schema v3 在索引首次打开时原地修复 v2 留下的命令、技能名与直连 MCP 行。v2 的代理 run 关联字段（`parent_call_id`、`agent_id`）仍需完整重读才能回填，所以从旧索引升级后请运行一次 **`qcut sync --full`**；它会先给旧索引做快照。全新安装无需额外步骤。

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
qcut sync --full         # ♻️  从零重建数据库（也会回填 v2 升级）
qcut stats               # 📊  各类别总览
qcut tools|skills|plugins|mcp [--all]   # 默认只列用过的；--all = 已安装 ∪ 用过
qcut agents [--all]      # 🤖  按类型的 run 成本账
qcut projects [PATH]     # 📁  项目板；PATH 下钻到该项目的会话
qcut models              # 🔢  按模型的用量合计、token 与积分
qcut commands            # ⌨️   斜杠命令用量，按调用数排序
qcut env                 # 🔑  token 开关，只读建议
qcut show tool Bash      # 🔍  某个实体的调用；索引从没见过它就 rc 1
qcut recent              # 🕒  最近的工具调用
qcut inventory           # 📦  已安装清单，用过 vs 未用
qcut export              # 💾  把每张表导出为 JSON
qcut health              # 🩺  数据库与数据源自检
qcut backup              # 💾  高风险操作前做快照
qcut restore             # ⏪  列出快照；`qcut restore NAME` 回滚
qcut vacuum              # 🧹  压实索引；先快照，后校验
qcut format              # 🧾  本工具依赖的 Qoder 字段与路径
```

| 变量 | 默认值 | 用途 |
|---|---|---|
| `QCUT_DB` | `~/.local/share/qoder-cn-usage-tracker/usage.db` | 数据库位置 |
| `QCUT_QODER_DIR` | `~/.qoder-cn` | 转录来源目录 |
| `QODERCN_EXPOSE_TOKEN_USAGE` | 未设置 | 由 **Qoder CN CLI** 读取，而非 qcut——设为 `1` 后托管调用会记录真实 token |

## ⚙️ 配置

每个开关都有它出厂时的取值，配置文件是可选的：在你不添加它之前，`qcut` 的行为与文档完全一致。

`~/.config/qcut/config.toml` —— 或用 `QCUT_CONFIG` 指向别处：

```toml
top_n = 5            # 仪表盘 Top 模型 / Top 工具的条数
log_limit = 100      # 用量页的请求行数
detail_limit = 200   # 历史页的行数
name_cap = 28        # 表格渲染单元格的最宽字符数
```

- **优先级为 环境 > 文件 > 默认值。** 每个键也可从 `QCUT_TOP_N`、`QCUT_LOG_LIMIT`、`QCUT_DETAIL_LIMIT`、`QCUT_NAME_CAP` 读取。没有刷新或同步周期：TUI 完全由事件驱动。
- **文件损坏会中止 TUI**，并给出路径与原因，而不是悄悄用默认值启动。`qcut health` 给出同样的结论。
- **未知键会被拒绝**，取值有类型与范围校验。
- 由标准库 `tomllib` 解析，因此**不引入任何依赖**。该文件从不被写入，也没有任何设置被存进数据库。

## 🔒 隐私

`qcut` 只读取 Qoder 自身转录中的**结构化元数据**字段，存储计数、耗时、状态、token、积分与标识符。它**不**读取或存储提示词 / 回复正文、工具参数**取值**或文件内容。唯一一次从自由文本里读出的东西是斜杠命令的**名字**：它取自 user 记录上的 `<command-name>` 标记，只留标识符本身，标记前后的正文一律丢弃；模型在自己的回答里引用同一个标记时，不算一次调用。🔒 一切都不出本机。

其中三条承诺由检查而非善意保证——见 [`AGENTS.md`](AGENTS.md#invariants-and-how-to-check-them)。

## 🧪 测试

```bash
python3 -m unittest discover -s scripts/tests     # Ran 405 tests ... OK
```

测试套件是 `unittest`（`pytest` 也能发现它们）。其中两套需要安装 `textual`：`test_tui.py`，以及 `test_config.py`——后者的接线用例会把 TUI 模块加载起来，用以证明每个设置项真的到达渲染器。每个套件都在临时目录里自建一次性数据库**以及**一次性 Qoder 目录树——你真实的 `usage.db` 与 `~/.qoder-cn` 从不会被打开，仓库中也没有任何代码路径会访问网络。

| 套件 | 用例数 | 覆盖范围 |
|---|---:|---|
| `test_sync.py` | 68 | 转录解析、归类（工具 / 技能 / 代理 / MCP，含直接以 `mcp__server__tool` 为工具名的形态）、路由来源、清单、增量与全量重建、被改写或结尾未换行的转录、带时间戳的状态配对、未认领记录统计、`model_calls` 只在真正插入时计数一次、Skill 结果自带的 `commandName` 不会被当成斜杠命令、生成的格式文档包含 token 捕获小节、schema 里**每一张表**的幂等性、sidechain 回复只经 Qoder 自己的 `parent_tool_use_id` 关联到发起它的 run、只解析 `compact_boundary` 的元数据而不碰其正文、读不了的转录会被报告并在下次重试、崩溃安全 |
| `test_cross_repo_golden.py` | 16 | 跨仓 golden fixture：一组固定输入（NULL / 部分上报 / cache 列缺失的 token 行、不同 model 拼写、本地午夜与跨小时边界、2026 年两个美国夏令时切换日）经过本仓自己的 Usage Total 表达式、窗口聚合、模型 rollup、日期时间输入与自然日窗口，钉在与 cbut、ocut 共享的权威文件上 |
| `test_usage.py` | 72 | 自然日窗口、供应商标签、缓存命中率、全部查询辅助函数、「已安装 ∪ 用过」并集、区分真实测量与历史零值的 token 标记、按供应商与工具过滤的历史、斜杠命令排行、把「实体不存在」与「本窗口内没用过」分开的判定、只通过显式链接触发的 run 成本聚合、项目 / 会话 / 请求三级账本、压缩摘要及其上下文税、schema（含增量 v1→v2 迁移与 v3 的命令/技能/MCP 修复）、最小版的 snapshot→list→restore 往返、尚无数据库的全新安装，以及无头报表的措辞 |
| `test_stats.py` | 56 | 无头 CLI 的端到端：每个子命令都真的调用 `main([...])`，`--db`、`--limit` 与 `--all`、只读连接、索引过期时的拒绝并指出修法、`export` / `inventory` / `recent` / `show` / `health`、`agents` 与 `projects` 账本（包括从未存在过的项目）、`commands` 报表、`env` 在开关设置与未设置两种情况下的输出、在临时库上跑 `vacuum`（快照、`quick_check`、行数不变）、损坏设置文件报成一句话而不是抛栈、`backup`/`restore` 这一对、`format --write` 只在标记之间重写并**拒绝**追加进丢了标记的文档、`show` 拒绝从没见过这个名字（而不是画成一片零）、索引真实覆盖的时间跨度，以及入口脚本绝不碰它没被给过的路径 |
| `test_snapshots.py` | 14 | 破坏性步骤的回退之路：快照在改动**之前**取、是在线备份而非把还在 `-wal` 里的主文件按字节拷贝、restore 保留它覆盖掉的状态、回收只删自己生成的文件，以及丢了偏移量的索引仍然值得一份快照 |
| `test_config.py` | 19 | 设置层：默认值、文件、环境优先级与边界、损坏文件指名路径、`qcut health` 打印的键，以及**每个声明的旋钮都有渲染器真的去读**——用户改了却毫无效果的设置项，就是这一条在拦 |
| `test_privacy.py` | 15 | 每个生产脚本**整份导入面**的白名单，外加导入清单看不见的两件事：经由已声明模块发起的调用（`os.system`），以及写在字符串里的模块名；证明源目录未被改写的指纹、跨所有文本列的哨兵串扫描，以及每项检查各自的正向对照 |
| `test_tui.py` | 74 | 用 Textual 自带 `run_test` 验证标签接线、缩放这条轴（`on_resize` 重新规划每个表格：窄屏保留的列必须是登记表的前缀并说出藏了哪些，行列与行 key 都要活过重建）、点击节奏（两条 `pilot.click` 之间不 pause 就是交给调度器掷硬币）、每标签独立范围、插件页无窗口、列计划、空状态、默认只显示用过的行与 `a` 切换到「已安装 ∪ 用过」、下钻（含按供应商限定的 Token 页与 server+tool 的 MCP 路由）、代理类型 → run 历史 → 请求、仪表盘项目 → 会话 → 请求两条梯子（NULL 项目自成一组的判定）、两根度量轴上逐行的 `—`/`-`/数字区分、压缩说明、标签名与画布底色这两条外观约定、**每个页面自己的表格由谁路由**（被推入的页面的行不得被路由两次）、**照搬 cbut 的日期选择器**（六个快捷键按钮、79×30 的宽敞几何、矮终端下滚动）、共享的 `s` / `/` / `a` / `d` 按键（每标签独立排序循环、常驻且每次编辑都回到首个匹配的过滤栏、没有内容时屏幕本身不能滚动）、启动只同步一次且没有后台定时器、推入的详情页独占按键，以及**真正显示在屏幕上**（而非仅仅存有文本）的状态行与仪表盘图例（各自只占一行，宽度不够就截断而不换行） |
| `test_dispatcher.py` | 9 | `bin/qcut` 路由、参数透传，以及它之所以用 bash 的那些失败模式 |
| `test_format_registry.py` | 10 | 用 AST 把格式登记表与它旁边的解析器互为镜像：读到的字段必须登记、登记的字段必须仍在读、分发链与登记表声称认领同一组记录类型，且分隔符 / 路径段 / 工具名都不许在调用点手打 |
| `test_maintenance_docs.py` | 11 | 生成的格式文档等于 `qcut format` 今天打印的内容、每个维护资产都能从路由文档抵达、每条链接都能解析，以及 CI 矩阵钉住的正是 `pyproject.toml` 声明的下限 |
| `test_hermetic.py` | 13 | 让套件远离真实数据库和 Qoder 主机目录的绊线：受保护路径会被拒绝，临时路径可放行，子进程钩子端到端生效，并要求子进程钉上 `QCUT_DB` 与 `QCUT_QODER_DIR` |
| `test_readme_counts.py` | 6 | 两份 README 里每一个用例数字——每行、每个总数——都等于从套件语法树上读下来的数量；盘上有套件却没有对应行，与行指向已删除的文件，同样判红 |
| `test_readme_bilingual.py` | 7 | 两份 README 是同一份文档的两种语言：标题表情符号序列与其顺序必须一致，因此任何一侧单独增 / 删 / 移动小节都会判红；每条页内链接都指向真实标题 |
| `test_record_coverage.py` | 6 | 改动生产脚本的提交必须在 `CHANGELOG.md` 里留下 `D-###` 条目；覆盖窗口从 `git log` 枚举而非手打，编号必须连续无缺，且每个被引用的哈希都要能解析 |
| `test_conventions.py` | 9 | A 档根文件层是否在根目录齐备、AGENTS.md 是否用一行指明档位而不是抄规范正文，以及忽略清单覆盖的运行态是否真的没有被跟踪 |

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
| `scripts/tests/` | 上述 405 个用例 |
| `docs/maintenance/` | 自动生成的格式面与体积性能基线 |
| `systemd/` | 可选的每日同步 service + timer |
| `requirements.txt` | `textual>=8.2,<9` —— 仅 TUI 需要；其余全部是标准库 |

两条路径都可在沙箱安装中覆盖：启动器用 `QCUT_SCRIPTS` 与 `QCUT_VENV`，数据用 `QCUT_DB` 与 `QCUT_QODER_DIR`。

## ⚠️ 局限与路线图

- 🔌 **MCP 用量天然稀疏**，直到你真的调用 MCP 工具；本机是 3 个服务共 18 次调用。稀疏的 MCP 页是诚实的，不是坏了。
- 🧩 **插件归属基于证据。** 名为 `plugin:skill` 的技能（或某插件安装的技能）会记到该插件名下；插件自带的代理与命令会从其安装目录列出，但**不**从转录中归属，因为 Qoder 并未在那里记录这种归属关系。
- 🔑 **历史托管调用会一直显示破折号。** 没有 `QODERCN_EXPOSE_TOKEN_USAGE=1` 时写入的托管调用存下的是无法修复的零；它们的 token 单元格显示 `—`，绝不估算，积分仍然真实。只有设置该变量之后的调用才带托管 token——见 [Token 捕获](#-token-捕获)。
- ⏱️ **`cbut` 里有三列在这里做不出来，也不给它们占位。** 一条模型响应只带一个时间戳，没有起止配对，所以**单次请求耗时**无法计算——`model_calls` 里根本没有耗时列（工具 / 技能 / 代理 / MCP 的行有，那是与各自结果配对出来的）。Qoder 也不为每次请求写 **HTTP 状态**。积分同样不折算成**钱**：转录里只有积分数值、没有单价，货币数字只能是本工具自己编一个常量再做乘法。这些是 Qoder 记录内容的缺失，不是读取器的漏项；一列破折号会被读成「这里有值，只是恰好是空」。
- 📅 **日期区间选择器照搬 cbut，连六个快捷键也一样。** 它为 79×30，恰好放进
  80×30 终端；更窄的终端会横向滚动弹窗，更矮的终端会纵向滚动。预设按钮是 cbut 的六个
  （`Today`、`1d`、`7d`、`14d`、`30d`、`All time`）；下拉框里额外的 2 天 / 3 天范围留在下拉框，
  仍可通过月历表达。选出的区间只属于当前标签，插件页始终保持全部时间。
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

## 🤝 参与贡献

这是个人工具，没有路线图，也没有评审队列。问题与补丁仍然欢迎：开 issue 时附上 `qcut health` 的输出
和你运行的版本。提交代码前先读 [AGENTS.md](AGENTS.md)——里面的不变量（只读访问、一套解析、
积分与 token 分开、八个 tab 及其按键）是补丁必须守住的。

## 🙏 致谢

`qcut` **模仿** [`cbut` / `codebuddy-usage-tracker`](https://github.com/SHADE-glitch/codebuddy-usage-tracker)——它跟踪 CodeBuddy Code 的同类维度；见[与 cbut 的关系](#-与-cbut-的关系)。架构一致（读取宿主已经写下的东西），但 schema 是 Qoder 自己的：用积分而非供应商上报合计，用 `Skill`/`Agent` 工具调用而非标记解析，MCP 写在参数里而非工具名里。它是模仿而非移植：只有 Qoder CN CLI 能填的类别才会保留。

## 📄 许可

MIT —— 见 [LICENSE](LICENSE)。© 2026 SHADE-glitch
