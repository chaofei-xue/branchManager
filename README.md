# branchManager

一个面向 Git 分支日常操作的交互式脚本，入口文件是 [git_branch_manager.py](/Users/xue/file/claude/branchManager/git_branch_manager.py)。

当前支持的主要流程：
- 创建开发分支：`feature_*` / `bugfix_*`
- 创建集成分支：`dev_*` / `release_*`
- 向集成分支追加开发分支
- 同步已集成开发分支的新提交
- 处理冲突，并在启用 `rerere` 时自动复用历史解决方案
- 删除分支
- 将 `release_*` 合并回 `master` / `main`

## 运行脚本

在 Git 仓库目录中执行：

```bash
python3 /Users/xue/file/claude/branchManager/git_branch_manager.py
```

## 自动化测试

项目里已经提供了一个正式的 `unittest` 端到端测试用例：

- 测试文件：[tests/test_branch_manager_e2e.py](/Users/xue/file/claude/branchManager/tests/test_branch_manager_e2e.py)
- 验证脚本：[scripts/validate_branch_manager.py](/Users/xue/file/claude/branchManager/scripts/validate_branch_manager.py)

执行方式：

```bash
python3 -m unittest /Users/xue/file/claude/branchManager/tests/test_branch_manager_e2e.py
```

或者直接运行验证脚本：

```bash
python3 /Users/xue/file/claude/branchManager/scripts/validate_branch_manager.py
```

## 覆盖场景

当前自动化验证覆盖以下关键路径：
- 创建两个开发分支
- 创建一个集成分支并批量合入开发分支
- 在两个开发分支上制造同文件同位置冲突
- 更新集成分支时，验证“一个成功、一个冲突后放弃”的路径
- 更新集成分支时，验证手动解决冲突后继续合并的路径
- 重放同一冲突，验证 `rerere` 自动复用并自动提交的路径
- 校验追踪提交 `[DREO-MERGE] ...` 只记录成功合并的分支

## 临时测试仓库

测试会在下面这个目录里反复重建临时 Git 仓库：

```text
/Users/xue/file/claude/branchManager/.tmp_branch_manager_validation
```

脚本不会修改你当前业务仓库中的 Git 历史，但会覆盖这个临时目录里的内容。
