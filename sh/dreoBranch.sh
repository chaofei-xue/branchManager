#!/bin/bash

# 分支集成管理脚本 开始
git reset --hard
git clean -df
git checkout -B "${branch#origin/}" "origin/${branch#origin/}"

# 执行脚本并将输出保存到临时文件
set +e
"$HOME/.local/bin/dreo_branch_operate" 2 2 "${branch#origin/}" --push > dreo_tmp.log 2>&1
code=$?
set -e

# 输出完整日志，读取最后一行校验结果，并清理临时文件
cat dreo_tmp.log
result=$(tail -n 1 dreo_tmp.log)
rm -f dreo_tmp.log

# 校验状态码与最后一行标识
if [ $code -eq 0 ] && [ "$result" = "DREO_RESULT=SUCCESS" ]; then
  echo "自动更新集成分支成功"
else
  echo "自动更新集成分支失败"
  exit 1
fi
# 分支集成管理脚本 结束
