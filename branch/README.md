# Dreo Branch Manager
[飞书文档-Dreo App 分支集成管理脚本使用指南](https://hesung2020.feishu.cn/wiki/I1aGwRYGkiCfLykx2Imcfi8mnth)

`Dreo Branch Manager` 是一套用于 Git 分支流转管理的脚本，包含 3 个核心文件：

- [dreo_branch_install.py](./dreo_branch_install.py)
- [dreo_branch_manager.py](./dreo_branch_manager.py)
- [dreo_branch_operate.py](./dreo_branch_operate.py)

适用场景：

- 创建开发分支：`feature_*` / `bugfix_*`
- 创建与更新集成分支：`dev_*` / `release_*`
- 合并 `master` 到当前分支
- 合并集成分支到 `master`
- 生成分支处理报告
- 交互式使用，或在 Jenkins / CI 中参数化调用

## 目录说明

- [dreo_branch_manager.py](./dreo_branch_manager.py)
  交互式主脚本，适合日常人工操作。
- [dreo_branch_operate.py](./dreo_branch_operate.py)
  参数化入口，适合 Jenkins、CI 或脚本集成。
- [dreo_branch_install.py](./dreo_branch_install.py)
  安装器，负责安装、更新、卸载脚本。

## 安装

在脚本目录执行：

```bash
python3 ./dreo_branch_install.py
```

安装器会提示你选择：

1. 安装：把脚本安装到用户目录
2. 更新：更新已安装脚本
3. 卸载：移除已安装脚本与 PATH 配置

安装后默认会生成这些命令：

```bash
dreo_branch_manager
branch
dbm
dreo_branch_operate
```

如果当前终端还没生效，可以执行：

```bash
source ~/.zshrc
```

或者重新打开终端。

## 交互式使用

运行：

```bash
branch
```

当前主菜单功能如下：

1. 创建开发分支（feature / bugfix）
2. 集成分支管理（测试 / 生产）
3. 拉取远端分支到本地
4. 合并 `master` 到当前分支
5. 合并集成分支到 `master`
6. 更新脚本
7. 生成分支处理报告
8. 删除分支

### 常用流程

#### 1. 创建开发分支

适合新功能或缺陷修复。

- 支持 `feature` / `bugfix`
- 支持基于 `master` 或当前分支创建
- 成功后可选择是否推送远端

#### 2. 创建集成分支

用于提测、联调或准备发布。

- 支持 `dev` / `release`
- 会先同步最新 `master`
- 可直接把多个开发分支集成进去

#### 3. 更新集成分支

用于同步已集成开发分支的新提交。

- 会先同步目标集成分支到远端最新
- 再基于已记录的集成关系更新开发分支
- 最后同步最新 `master`
- 更新时优先使用远端开发分支
- 已删除的历史开发分支只会提示警告，不会导致整个流程失败

#### 4. 生成分支处理报告

会在当前 Git 仓库生成：

- `branch_merge_report.html`
- `branch_merge_report.md`

HTML 报告包含：

- 时间线
- 分支流转图
- 追踪提交信息

## 参数化使用

运行：

```bash
dreo_branch_operate --help
```

适合 Jenkins、CI、自动化脚本调用。

### 常用示例

创建开发分支：

```bash
dreo_branch_operate 1 feature test1 master
```

创建集成分支：

```bash
dreo_branch_operate 2 1 dev 3.6.0 feature_a_20260415 feature_b_20260415
```

更新集成分支：

```bash
dreo_branch_operate 2 2 dev_3.6.0_20260415
```

更新集成分支并推送：

```bash
dreo_branch_operate 2 2 dev_3.6.0_20260415 --push
```

追加新的开发分支到集成分支：

```bash
dreo_branch_operate 2 3 dev_3.6.0_20260415 feature_c_20260415
```

合并 `master` 到当前分支：

```bash
dreo_branch_operate 4 --push
```

合并发布分支到 `master`：

```bash
dreo_branch_operate 5 release_3.6.0_20260415 --push --delete-related
```

删除本地 + 云端分支：

```bash
dreo_branch_operate 7 2 feature_test1_20260415 feature_test2_20260415
```

### 参数模式返回结果

参数模式结束时，最后会固定输出：

```text
执行结果: 成功
DREO_RESULT=SUCCESS
```

或者：

```text
执行结果: 失败
DREO_RESULT=FAILED
```

可用于 Jenkins / CI 判断任务是否继续执行。

## Jenkins 示例

```bash
git reset --hard
git clean -df
git checkout -B "${branch#origin/}" "origin/${branch#origin/}"

set +e
operate_output=$("$HOME/.local/bin/dreo_branch_operate" 2 2 "${branch#origin/}" --push 2>&1)
operate_code=$?
set -e

echo "${operate_output}"

operate_result=$(printf '%s\n' "${operate_output}" | tail -n 1)

if [ ${operate_code} -ne 0 ] || [ "${operate_result}" != "DREO_RESULT=SUCCESS" ]; then
  echo "dreo_branch_operate 执行失败，终止 Jenkins 任务。"
  exit 1
fi
```

## 更新脚本

有两种方式：

### 方式 1：通过安装器更新

```bash
python3 ./dreo_branch_install.py
```

选择：

```text
2. 更新
```

### 方式 2：通过主菜单更新

运行：

```bash
branch
```

然后选择：

```text
6. 更新脚本
```

主脚本会自动：

- 从脚本关联的 Git 仓库拉取最新代码
- 调用安装器更新本地已安装脚本

## 版本号

版本来源：

- [dreo_branch_manager.py](./dreo_branch_manager.py) 中的 `APP_VERSION`

例如：

```python
APP_VERSION = "1.0.0"
```

如果你要升级版本，只需要修改这行，然后执行一次安装器更新即可。

## 注意事项

- 建议在干净工作区使用。
- 脚本会自动开启当前仓库的 `rerere`，帮助复用冲突解决方案。
- 集成分支更新时默认优先使用远端开发分支。
- 参数模式下如果发生冲突，会自动 `git merge --abort` 并返回失败。
- 交互模式更适合人工处理冲突，参数模式更适合自动化流程。
