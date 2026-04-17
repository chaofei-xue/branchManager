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
APP_VERSION = "1.0.2"
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

### 4.2 集成分支

集成分支命名规则：

- `dev_<name>_<YYYYMMDD>`
- `release_<name>_<YYYYMMDD>`

支持：

- 创建集成分支
- 更新集成分支
- 添加新的开发分支到集成分支

### 4.3 更新集成分支的当前逻辑

这是当前维护时最重要的一条逻辑。

“更新集成分支”现在的顺序是：

1. `fetch` 远端引用
2. 选择目标集成分支
3. 读取该集成分支历史上的 `[DREO-MERGE]` 记录，找出已集成的开发分支
4. **先同步本地 `master/main` 到远端最新**
5. 再将最新本地主干合并到集成分支
6. 再逐个同步开发分支新增提交

主干同步使用的是：

- 先 `checkout_and_update_base(base)`
- 即先切到本地 `master/main`
- 再 `git pull <remote> <base>`
- 然后回到集成分支执行合并

这里之前有过一个 bug：

- 只比较本地 `master`
- 导致远端 `master` 有新提交时仍提示“master 已是最新”

这个问题已经修掉了，当前逻辑是“先更新本地主干，再合并主干”。

### 4.4 开发分支更新来源

更新集成分支时，开发分支是：

- **远端优先**
- 本地兜底

即：

- 如果有 `origin/<branch>`，优先用远端
- 没有远端时才用本地分支

这也是为了避免本地分支落后导致漏合并。

### 4.5 `[DREO-MERGE]` 追踪提交规则

当前规则：

- 创建集成分支时会写
- 向集成分支首次添加新的开发分支时会写
- **更新集成分支时不会再写**

之所以这么做，是为了避免每次同步开发分支新增提交时都插入一条空追踪提交，导致历史非常难看。

### 4.6 冲突处理

交互模式：

- 用户可手动解决冲突
- 支持继续合并或放弃

参数模式：

- 一旦冲突，立即 `git merge --abort`
- 当前任务判定失败

### 4.7 `rerere`

当前脚本执行时，会自动为当前仓库开启：

```bash
git config --local rerere.enabled true
```

不再弹用户确认。

### 4.8 报告生成

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

### 4.9 自更新

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

- `1 <feature|bugfix> <name> [master|current]`
- `2 1 <dev|release> <version> <branch...>`
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

- `Ran 47 tests`

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
  - 再同步主干到集成分支
  - 再同步开发分支
- 更新集成分支时不再写新的 `[DREO-MERGE]`
- 参数模式最后两行输出必须稳定
- 报告模板代码与主逻辑已经分离，不建议再把大段 HTML 塞回主脚本

### 9.2 容易出问题的区域

- 终端输入与中文输入法兼容
- 分页交互
- 远端分支优先级
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
