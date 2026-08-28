# contract-check MySQL 备份（单用户内网最小运维集，Windows）
# 用法: powershell -ExecutionPolicy Bypass -File backend/scripts/backup_mysql.ps1
# 产物: data/backups/contract_check_YYYYMMdd_HHMMSS.sql.gz，保留最近 14 份
# 定时: 任务计划程序每日跑一次即可
#
# 凭证来源（仓库不存凭据）: infra\.env 的 MYSQL_CONTRACT_USER / MYSQL_CONTRACT_PASSWORD，
# 也可用环境变量 / 参数覆盖：-InfraDir / -BackupDir / -Keep / -Container / -Database。
param(
    [string]$InfraDir,
    [string]$BackupDir,
    [int]   $Keep      = 14,
    [string]$Container = "shared-mysql",
    [string]$Database  = "contract_check"
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
if (-not $InfraDir)  { $InfraDir  = Join-Path (Split-Path $RepoRoot -Parent) "infra" }
if (-not $BackupDir) { $BackupDir = Join-Path $RepoRoot "data\backups" }

# 凭证：优先环境变量，其次 infra\.env
$User = $env:MYSQL_CONTRACT_USER
$Pass = $env:MYSQL_CONTRACT_PASSWORD
$envFile = Join-Path $InfraDir ".env"
if ((-not $User -or -not $Pass) -and (Test-Path $envFile)) {
    Get-Content $envFile | ForEach-Object {
        if ($_ -match '^\s*MYSQL_CONTRACT_USER=(.+)$')     { $User = $Matches[1] }
        if ($_ -match '^\s*MYSQL_CONTRACT_PASSWORD=(.+)$') { $Pass = $Matches[1].Trim('"').Trim("'") }
    }
}
if (-not $User) { $User = "contract" }
if (-not $Pass) { throw "未找到 MYSQL_CONTRACT_PASSWORD（设置环境变量或 infra\.env）" }

New-Item -ItemType Directory -Force -Path $BackupDir | Out-Null
$Stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$Out   = Join-Path $BackupDir "$Database`_$Stamp.sql.gz"

# gzip 在容器内执行（mysql:8.0 自带），宿主机无需安装；密码走 -e MYSQL_PWD
docker exec -e "MYSQL_PWD=$Pass" -e "MYSQL_CONTRACT_USER=$User" -e "CC_DB=$Database" $Container sh -c `
    'mysqldump --single-transaction --routines --triggers --no-tablespaces -u "$MYSQL_CONTRACT_USER" "$CC_DB" | gzip' `
    > $Out

if (-not (Test-Path $Out) -or (Get-Item $Out).Length -eq 0) { throw "备份失败：$Out 为空或未生成" }

# 轮转：只保留最近 Keep 份（只删本库 gzip，不递归）
Get-ChildItem -Path $BackupDir -Filter "$Database`_*.sql.gz" `
    | Sort-Object Name -Descending `
    | Select-Object -Skip $Keep `
    | Remove-Item -Force -Confirm:$false

Write-Host "备份完成: $Out（保留最近 $Keep 份）"
