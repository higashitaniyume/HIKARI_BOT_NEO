# HIKARI_BOT_NEO 一键部署脚本
# 使用方式：在 Windows PowerShell 中运行 .\deploy.ps1
# 前提：已配置 SSH key 免密登录 root@192.168.31.2

param(
    [string]$ServerIP = "192.168.31.2",
    [string]$ServerUser = "root",
    [string]$DeployPath = "/opt/HIKARI_BOT_NEO",
    [string]$ServiceName = "hikari-bot-neo"
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  HIKARI BOT NEO - 一键部署" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# ---- Step 1: 创建临时打包目录 ----
Write-Host "[1/8] 准备项目文件..." -ForegroundColor Yellow

$TempDir = Join-Path $env:TEMP "hikari_bot_deploy_$(Get-Date -Format 'yyyyMMddHHmmss')"
New-Item -ItemType Directory -Force -Path $TempDir | Out-Null

# 复制项目文件，排除不需要的目录
$ExcludeDirs = @(
    ".git",
    ".venv",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".claude",
    "BotData",
    "UserData"
)

Write-Host "  临时目录: $TempDir"

$SourceDir = $PSScriptRoot
Get-ChildItem -Path $SourceDir -Exclude $ExcludeDirs | Copy-Item -Destination $TempDir -Recurse -Force

Write-Host "  文件复制完成" -ForegroundColor Green

# ---- Step 2: 通过 scp 上传到服务器 ----
Write-Host "[2/8] 上传文件到服务器..." -ForegroundColor Yellow

# 确保服务器目标目录存在
ssh "${ServerUser}@${ServerIP}" "mkdir -p ${DeployPath}"

# 使用 scp 上传
scp -r "${TempDir}\*" "${ServerUser}@${ServerIP}:${DeployPath}/"

Write-Host "  上传完成" -ForegroundColor Green

# ---- Step 2.5: 同步表情包目录 ----
$GifsSource = Join-Path $SourceDir "BotData\Gifs"
if (Test-Path $GifsSource) {
    ssh "${ServerUser}@${ServerIP}" "mkdir -p ${DeployPath}/BotData/Gifs"

    # 优先用 rsync 增量同步（仅传新增/修改的文件），没有则退到 scp
    if (Get-Command rsync -ErrorAction SilentlyContinue) {
        Write-Host "[rsync] 增量同步表情包目录..." -ForegroundColor Yellow
        rsync -avz "$GifsSource/" "${ServerUser}@${ServerIP}:${DeployPath}/BotData/Gifs/"
        Write-Host "  表情包增量同步完成" -ForegroundColor Green
    }
    else {
        Write-Host "[scp] rsync 不可用，全量上传表情包..." -ForegroundColor Yellow
        Write-Host "  提示: 安装 Git for Windows 或 WSL 即可使用 rsync 增量同步" -ForegroundColor DarkGray
        scp -r "$GifsSource\*" "${ServerUser}@${ServerIP}:${DeployPath}/BotData/Gifs/"
        Write-Host "  表情包上传完成" -ForegroundColor Green
    }
}

# ---- Step 3: 清理临时目录 ----
Write-Host "[3/8] 清理临时文件..." -ForegroundColor Yellow
Remove-Item -Recurse -Force $TempDir
Write-Host "  已清理" -ForegroundColor Green

# ---- Step 4: 检查服务器上的 uv ----
Write-Host "[4/8] 检查 uv 安装..." -ForegroundColor Yellow
$uvCheck = ssh "${ServerUser}@${ServerIP}" "command -v uv || echo 'NOT_FOUND'"
if ($uvCheck -eq "NOT_FOUND") {
    Write-Host "  uv 未安装，正在安装..." -ForegroundColor Yellow
    ssh "${ServerUser}@${ServerIP}" "curl -LsSf https://astral.sh/uv/install.sh | sh"
    Write-Host "  uv 安装完成" -ForegroundColor Green
} else {
    Write-Host "  uv 已安装: $uvCheck" -ForegroundColor Green
}

# ---- Step 5: 执行 uv sync ----
Write-Host "[5/8] 安装 Python 依赖 (uv sync)..." -ForegroundColor Yellow
ssh "${ServerUser}@${ServerIP}" "cd ${DeployPath} && uv sync"
Write-Host "  依赖安装完成" -ForegroundColor Green

# ---- Step 6: 安装/更新 systemd service ----
Write-Host "[6/8] 安装 systemd 服务..." -ForegroundColor Yellow
ssh "${ServerUser}@${ServerIP}" "cp ${DeployPath}/hikari-bot-neo.service /etc/systemd/system/"
ssh "${ServerUser}@${ServerIP}" "systemctl daemon-reload"
Write-Host "  systemd 服务文件已安装" -ForegroundColor Green

# ---- Step 7: 启用并重启服务 ----
Write-Host "[7/8] 启动服务..." -ForegroundColor Yellow
ssh "${ServerUser}@${ServerIP}" "systemctl enable ${ServiceName}"
ssh "${ServerUser}@${ServerIP}" "systemctl restart ${ServiceName}"
Write-Host "  服务已启动" -ForegroundColor Green

# ---- Step 8: 显示状态 ----
Write-Host "[8/8] 检查服务状态..." -ForegroundColor Yellow
Write-Host ""
ssh "${ServerUser}@${ServerIP}" "systemctl status ${ServiceName} --no-pager -l"
Write-Host ""

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  部署完成！" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "常用命令：" -ForegroundColor White
Write-Host "  查看日志:  ssh ${ServerUser}@${ServerIP} journalctl -u ${ServiceName} -f" -ForegroundColor Gray
Write-Host "  查看状态:  ssh ${ServerUser}@${ServerIP} systemctl status ${ServiceName}" -ForegroundColor Gray
Write-Host "  重启服务:  ssh ${ServerUser}@${ServerIP} systemctl restart ${ServiceName}" -ForegroundColor Gray
Write-Host "  停止服务:  ssh ${ServerUser}@${ServerIP} systemctl stop ${ServiceName}" -ForegroundColor Gray
Write-Host ""
