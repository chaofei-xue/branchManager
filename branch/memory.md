# Dreo Branch Manager 脚本交接说明

本文档用于给下一个 agent 或维护者快速接手当前脚本。

## 1. 当前目录结构

当前核心代码位于 `branch/` 目录：

- `dreo_branch_manager.py`
  交互式主脚本
- `dreo_branch_operate.py`
  参数化入口，适合 Jenkins / CI
- `dreo_branch_install.py`
  安装、更新、卸载脚本
- `branch_report_templates.py`
  分支处理报告的 HTML / Markdown 模板与图表渲染
- `README.md`
  简版使用说明

辅助目录：

- `tests/`
  自动化测试
- `scripts/validate_branch_manager.py`
  端到端验证脚本

## 2. 版本号规则

版本号现在是手动维护，不再使用 Git SHA 自动显示。

版本来源：

- `dreo_branch_manager.py` 中的：

```python
APP_VERSION = "1.0.4"
```

更新版本时，只需要修改这一个值，然后重新执行安装器更新即可。

安装器会把版本号写入安装元信息文件：

- `~/.local/share/dreo_branch_manager/dreo_branch_manager_meta.json`

主脚本启动时优先显示安装元信息里的 `source_version`，如果没有，再回退到源码中的 `APP_VERSION`。

## 3. 三个脚本的职责

### 3.1 `dreo_branch_manager.py`

这是主入口，负责：

- 终端 UI
- Git 操作
- 分支逻辑
- 报告数据整理
- 自更新入口

当前主菜单顺序：

1. 创建开发分支（feature / bugfix）
2. 集成分支管理（测试 / 生产）
3. 拉取远端分支到本地
4. 合并 `master` 到当前分支
5. 合并集成分支到 `master`
6. 更新脚本
7. 生成分支处理报告
8. 删除分支
0. 退出

### 3.2 `dreo_branch_operate.py`

这是参数化入口，复用主脚本里的业务逻辑，主要用于：

- Jenkins
- CI
- 非交互自动化

特点：

- 通过 `mock.patch` 替换主脚本中的交互函数
- 冲突时自动 `git merge --abort`
- 最终固定输出：

```text
执行结果: 成功
DREO_RESULT=SUCCESS
```

或：

```text
执行结果: 失败
DREO_RESULT=FAILED
```

外部环境应该同时结合：

- 进程退出码
- 最后一行 `DREO_RESULT=...`

做最终判断。

### 3.3 `dreo_branch_install.py`

安装器负责：

- 将脚本复制到 `~/.local/share/dreo_branch_manager/`
- 在 `~/.local/bin/` 下生成命令：
  - `dreo_branch_manager`
  - `branch`
  - `dbm`
  - `dreo_branch_operate`
- 修改 shell 启动文件，把 `~/.local/bin` 加入 PATH
- 写入安装元信息
- 卸载时清理副本和受管 PATH 配置

## 4. 当前脚本的关键功能与约定

### 4.1 开发分支

开发分支命名规则：

- `feature_<name>_<YYYYMMDD>`
- `bugfix_<name>_<YYYYMMDD>`

支持：

- 从 `master` 创建
- 从当前分支创建（不建议）

如果从当前分支创建，报告会尝试按真实来源分支描述，不再一律写成“从 master 拉出”。

创建开发分支时，输入分支名后还会要求输入「版本描述」：

- **不允许为空**，输入空会持续提示重新输入
- 在新分支上写入一条空提交 `[DREO-DESC]<描述>`
- 远端已有同名分支被恢复到本地时，不会再写入新的描述提交（沿用远端历史）
- 参数模式 (`dreo_branch_operate 1 ...`) 通过 `--desc "<描述>"` 传入（必填）

### 4.2 集成分支

集成分支命名规则：

- `dev_<name>_<YYYYMMDD>`
- `release_<name>_<YYYYMMDD>`

支持：

- 创建集成分支
- 更新集成分支
- 添加新的开发分支到集成分支

创建 `release` 分支时，如果当前存在 `dev_*` 集成分支，会在版本号输入后多一个步骤让用户选择集成来源：

1. **手动选择开发分支** — 走与 `dev` 相同的流程
2. **从已有 dev 集成分支继承** — 用户选择一个 `dev` 分支，脚本通过 `get_merged_feature_branches(dev_branch)` 自动识别该 dev 分支的已集成开发分支列表，作为 `feature_branches` 候选列表传入后续流程

如果没有任何 `dev_*` 分支存在，release 直接进入选择开发分支的流程，不弹选择来源。

参数模式通过 `--from-dev <dev分支名>` 指定继承源；不提供 `--from-dev` 时与 dev 一样需要显式传入开发分支列表。

创建集成分支时，会在合并完成后聚合所选开发分支的 `[DREO-DESC]` 描述：

- 读取每个开发分支最近一条 `[DREO-DESC]` 描述（远端优先、本地兜底）
- 原样拼接，逗号隔开，写入一条空提交：`[DREO-DESC]<desc1>,<desc2>,...`
- 所选开发分支均无描述时跳过该提交，并给出提示
- 仅在「创建集成分支」阶段写入；「更新集成分支」与「添加新的开发分支到集成分支」当前不会再次写入集成描述提交

### 4.3 更新集成分支的当前逻辑

这是当前维护时最重要的一条逻辑。

“更新集成分支”现在的顺序是：

1. `fetch` 远端引用
2. 选择目标集成分支
3. 读取该集成分支历史上的 `[DREO-MERGE]` 记录，找出已集成的开发分支
4. **先同步目标集成分支到远端最新**
5. 再逐个同步开发分支新增提交
6. 再同步本地 `master/main` 到远端最新
7. 最后将最新本地主干合并到集成分支

主干同步使用的是：

- 先 `checkout_and_update_base(base)`
- 即先切到本地 `master/main`
- 再 `git pull --ff-only <remote> <base>`
- 然后回到集成分支执行合并

注意：更新集成分支时，目标集成分支只会在合并开发分支前同步到远端最新；合完开发分支后再同步主干时，不会再次对集成分支执行 `pull --ff-only`，避免本地刚产生的开发分支合并提交与远端状态判断互相干扰。

这里之前有过一个 bug：

- 只比较本地 `master`
- 导致远端 `master` 有新提交时仍提示“master 已是最新”

这个问题已经修掉了，当前逻辑是“合完开发分支后，再更新本地主干并合并主干”。

当前集成分支自身在执行更新前，也会先同步到远端最新：

- `sync_local_branch_with_remote(int_branch)`

如果本地集成分支与远端分叉、无法 fast-forward：

- **直接失败并停止**
- 不会再退回到“使用本地旧分支继续”

### 4.4 开发分支更新来源

当前所有“把开发分支合到集成分支”的路径，开发分支都是：

- **远端优先**
- 本地兜底

即：

- 如果有 `origin/<branch>`，优先用远端
- 没有远端时才用本地分支

覆盖范围包括：

- 创建集成分支时合并开发分支
- 向集成分支添加新的开发分支
- 更新集成分支时同步开发分支新增提交

这也是为了避免本地分支落后导致漏合并。

### 4.5 主干 / 目标分支同步策略

当前脚本与远端相关的关键分支操作，已经改为：

- **严格远端优先**
- **更新失败即停止**

这里的“更新失败即停止”主要指：

- `master/main` 同步时使用 `git pull --ff-only`
- 当前分支 / 集成分支 / release 分支同步时，也要求能 fast-forward 到远端最新
- 如果本地与远端发生分叉，脚本会报错并停止
- **不会**再提示“使用本地继续”

目前受这一策略约束的关键路径包括：

- 创建开发分支（基于 `master/main` 时）
- 创建集成分支
- 更新集成分支
- 添加新的开发分支到集成分支
- 合并 `master` 到当前分支
- 合并集成分支到 `master`

### 4.6 启动时开发分支落后提示

当前脚本启动进入主菜单前，会做一次预检查：

- 如果当前分支是 `feature_*` / `bugfix_*`
- 且当前开发分支落后于最新 `master/main`

则会提示用户：

- 当前开发分支落后主干多少个提交
- 是否立即执行“合并 `master` 到当前分支”

这里检查主干时也是：

- 远端优先
- 先 `fetch` 最新引用
- 有 `origin/master` / `origin/main` 时优先比较远端

如果用户选择立即执行：

- 直接复用“合并 `master` 到当前分支”的现有逻辑
- 不会再多弹一次“确认执行？”的重复确认

### 4.7 `release -> master` 的重复合并行为

当前“合并集成分支到 `master`”在正式 merge 之前，会先判断：

- 目标 `release` 是否已经是 `master` 的祖先

如果已经合并过：

- 提示“已合并到 master，已跳过此次操作”
- 不再重复提示“合并成功”
- 不会产生新的 merge commit

这里判断时同样是：

- 先同步本地 `release` 到远端最新
- 再用远端优先的 `release` 引用做祖先判断

### 4.8 `[DREO-MERGE]` 追踪提交规则

当前规则：

- 创建集成分支时会写
- 向集成分支首次添加新的开发分支时会写
- **更新集成分支时不会再写**

之所以这么做，是为了避免每次同步开发分支新增提交时都插入一条空追踪提交，导致历史非常难看。

### 4.8.1 `[DREO-DESC]` 描述提交规则

当前规则：

- 创建开发分支时，用户可以输入「版本描述」(空则跳过)；非空写入一条空提交 `[DREO-DESC]<描述>` 到开发分支
- 创建集成分支时，会读取所选开发分支最近一条 `[DREO-DESC]` 描述，原样拼接逗号隔开写入集成分支：`[DREO-DESC]<desc1>,<desc2>,...`
- **更新集成分支与添加新开发分支到集成分支时，当前不会再写**集成层面的描述提交（避免历史被反复覆盖）
- `get_branch_description(branch)` 用于读取分支最近一条 `[DREO-DESC]`，远端优先、本地兜底；用 `git log -F --grep=[DREO-DESC]` 匹配
- 关键常量：`DESC_TAG = '[DREO-DESC]'`

### 4.9 冲突处理

交互模式：

- 用户可手动解决冲突
- 支持继续合并或放弃

参数模式：

- 一旦冲突，立即 `git merge --abort`
- 当前任务判定失败

### 4.10 `rerere`

当前脚本执行时，会自动为当前仓库开启：

```bash
git config --local rerere.enabled true
```

不再弹用户确认。

### 4.11 报告生成

报告功能已经从主脚本中拆出模板模块：

- `branch_report_templates.py`

主脚本负责：

- 收集报告数据
- 整理事件
- 传给模板渲染

模板模块负责：

- 时间线 Mermaid
- 分支流转图 SVG / HTML
- Markdown 渲染
- 追踪提交展示

当前会生成：

- `branch_merge_report.html`
- `branch_merge_report.md`

### 4.12 自更新

主菜单的“更新脚本”会：

1. 从安装元信息中读取源码仓库位置
2. 在源码仓库执行 `git fetch` + `git pull --ff-only`
3. 调用安装器更新当前已安装脚本

注意：

- 这个能力依赖安装时写入的元信息文件
- 如果是直接运行源码副本而未安装，自更新能力可能退回到当前仓库上下文

## 5. 远端分支相关约定

### 5.1 远端识别

当前脚本对开发/集成分支都支持：

- 本地分支
- 远端分支

并处理以下场景：

- 本地不存在、远端存在时，可直接恢复本地跟踪分支
- 删除分支时，支持删除“仅远端存在”的分支
- 拉取远端分支到本地时，支持分页选择

### 5.2 分页

当前分页规则：

- 每页 20 条
- 方向键上：上一页
- 方向键下：下一页
- Enter：确认
- 0：返回

删除分支和远端分支拉取都走这一套分页逻辑。

## 6. 参数模式约定

`dreo_branch_operate.py` 当前支持的菜单映射：

- `1 <feature|bugfix> <name> [master|current] --desc "<描述>"`
- `2 1 <dev|release> <version> <branch...>` 或 `2 1 release <version> --from-dev <dev分支名>`
- `2 2 <integration_branch>`
- `2 3 <integration_branch> <branch...>`
- `3 <remote_branch>`
- `4`
- `5 <release_branch> [--push] [--delete-related]`
- `6`
- `7 1 <branch...>`
- `7 2 <branch...>`

参数模式下的重要行为：

- 分支已存在时直接失败，不会进入交互重试
- 冲突直接失败
- `--push` 时会推送
- 历史关联开发分支已删除时，更新集成分支仍返回成功，只给 warning
- `--desc` 仅作用于菜单 `1` 创建开发分支且为必填；写入 `[DREO-DESC]<desc>` 描述提交

## 7. 安装与路径

默认安装位置：

- 主脚本：`~/.local/share/dreo_branch_manager/dreo_branch_manager.py`
- 参数脚本：`~/.local/share/dreo_branch_manager/dreo_branch_operate.py`
- 报告模板：`~/.local/share/dreo_branch_manager/branch_report_templates.py`
- 元信息：`~/.local/share/dreo_branch_manager/dreo_branch_manager_meta.json`

命令位置：

- `~/.local/bin/dreo_branch_manager`
- `~/.local/bin/branch`
- `~/.local/bin/dbm`
- `~/.local/bin/dreo_branch_operate`

## 8. 自动化测试

当前测试目录：

- `tests/test_branch_install.py`
- `tests/test_branch_manager_e2e.py`
- `tests/test_branch_operate.py`
- `tests/test_branch_report.py`
- `tests/test_remote_branch_support.py`
- `tests/test_terminal_input_helpers.py`

当前已验证通过的全量测试数：

- `Ran 63 tests`

常用测试命令：

```bash
python3 -m unittest discover -s ../tests -p 'test_*.py'
```

如果在仓库根目录执行：

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
```

## 9. 接手修改时优先注意的点

### 9.1 不要破坏的约定

- 更新集成分支时：
  - 先更新本地主干
  - 先同步目标集成分支到远端最新
  - 再同步主干到集成分支
  - 再同步开发分支
- 创建集成分支 / 添加开发分支到集成分支时：
  - 目标集成分支先同步远端最新
  - 开发分支合并来源必须保持“远端优先、本地兜底”
- `master/main`、当前分支、集成分支、release 分支只要需要和远端对齐：
  - 必须 fast-forward 成功
  - 失败就停止
  - 不能再“使用本地继续”
- 启动时如果当前在 `feature_*` / `bugfix_*` 且落后主干：
  - 要有提示
  - 并允许直接触发“合并 master 到当前分支”
- 重复执行 `release -> master` 时：
  - 已合并的 release 必须提示跳过
  - 不能重复报成功
- 更新集成分支时不再写新的 `[DREO-MERGE]`
- 创建开发分支时版本描述为必填，必须写入 `[DREO-DESC]<描述>` 空提交
- 创建集成分支时，必须聚合所选开发分支的 `[DREO-DESC]`，原样逗号拼接写入集成层 `[DREO-DESC]` 提交（全空时可跳过并提示）
- 「更新集成分支」与「添加新开发分支到集成分支」当前都不会写入集成层 `[DREO-DESC]`，避免反复覆盖历史
- 参数模式最后两行输出必须稳定
- 报告模板代码与主逻辑已经分离，不建议再把大段 HTML 塞回主脚本

### 9.2 容易出问题的区域

- 终端输入与中文输入法兼容
- 分页交互
- 远端分支优先级
- fast-forward 失败时的停止路径
- 启动提示与主菜单交互顺序
- 报告对“已删除分支”的容错
- 参数模式 `--push` 路径

### 9.3 如果后续继续开发

推荐优先补的方向：

- 报告数据分析进一步拆模块
- 把 README 与交接文档保持同步
- 如果新增菜单，同步更新：
  - `README.md`
  - `dreo_branch_operate.py --help`
  - 对应测试

## 10. 交接结论

当前脚本已经具备：

- 交互式分支管理
- 参数化自动化调用
- 自动安装 / 更新 / 卸载
- 分支处理报告生成
- 远端分支优先与删除容错

并且已通过现有自动化测试。

后续接手时，建议先从：

- `dreo_branch_manager.py`
- `dreo_branch_operate.py`
- `tests/test_remote_branch_support.py`

这三个文件开始建立上下文。
