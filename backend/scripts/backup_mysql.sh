#!/usr/bin/env bash
# contract-check MySQL 备份（单用户内网最小运维集）
# 用法: bash backend/scripts/backup_mysql.sh
# 产物: data/backups/contract_check_YYYYmmdd_HHMMSS.sql.gz，保留最近 14 份
# 定时: Windows 计划任务 / crontab 每日跑一次即可
#
# 凭证来源（仓库不存凭据）: infra/.env 的 MYSQL_CONTRACT_USER / MYSQL_CONTRACT_PASSWORD，
# 也可用环境变量覆盖：INFRA_DIR / BACKUP_DIR / KEEP / MYSQL_CONTAINER / MYSQL_DATABASE。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"                      # backend/scripts -> 项目根
INFRA_DIR="${INFRA_DIR:-$REPO_ROOT/../infra}"                      # 共享 infra（含 .env / shared-mysql）
BACKUP_DIR="${BACKUP_DIR:-$REPO_ROOT/data/backups}"
KEEP="${KEEP:-14}"
CONTAINER="${MYSQL_CONTAINER:-shared-mysql}"
DB="${MYSQL_DATABASE:-contract_check}"

# 从 infra/.env 读凭证（set -a 导入为环境变量）
if [ -f "$INFRA_DIR/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  . "$INFRA_DIR/.env"
  set +a
fi
: "${MYSQL_CONTRACT_USER:=contract}"
: "${MYSQL_CONTRACT_PASSWORD:?请设置 MYSQL_CONTRACT_PASSWORD（环境变量或 infra/.env）}"

mkdir -p "$BACKUP_DIR"
STAMP="$(date +%Y%m%d_%H%M%S)"
OUT="$BACKUP_DIR/${DB}_${STAMP}.sql.gz"

# gzip 在容器内执行（mysql:8.0 自带），宿主机无需安装；
# 密码走 docker exec -e MYSQL_PWD，不落进程列表/命令行
docker exec -e MYSQL_PWD="$MYSQL_CONTRACT_PASSWORD" \
  -e MYSQL_CONTRACT_USER="$MYSQL_CONTRACT_USER" -e CC_DB="$DB" \
  "$CONTAINER" sh -c \
  'mysqldump --single-transaction --routines --triggers --no-tablespaces -u "$MYSQL_CONTRACT_USER" "$CC_DB" | gzip' \
  > "$OUT"

# 轮转：只保留最近 KEEP 份（只删 BACKUP_DIR 内本库 gzip，不递归）
if command -v ls >/dev/null 2>&1; then
  ls -1t "$BACKUP_DIR"/"${DB}"_*.sql.gz 2>/dev/null \
    | tail -n +$((KEEP + 1)) | xargs -r rm -f
fi

echo "备份完成: $OUT（保留最近 $KEEP 份）"
