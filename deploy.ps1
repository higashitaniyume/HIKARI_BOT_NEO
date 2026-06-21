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

$SourceDir = $PSScriptRoot

# ---- Step 1: 增量上传表情包（先传，最快感知进度）----
Write-Host "[1/8] 增量同步表情包..." -ForegroundColor Yellow
$GifsSource = Join-Path $SourceDir "BotData\Gifs"
if (Test-Path $GifsSource) {
    ssh "${ServerUser}@${ServerIP}" "mkdir -p ${DeployPath}/BotData/Gifs"

    # 获取远程文件列表（相对路径|字节数）
    $remoteFiles = @{}
    $remoteList = ssh "${ServerUser}@${ServerIP}" "find ${DeployPath}/BotData/Gifs -type f -exec stat -c '%n|%s' {} \; 2>/dev/null"
    if ($remoteList) {
        $remoteList -split "`n" | ForEach-Object {
            if ($_ -match '\|(\d+)$') {
                $rp = $_ -replace "^${DeployPath}/BotData/Gifs/", "" -replace '\|\d+$', ''
                $remoteFiles[$rp] = [int64]($matches[1])
            }
        }
    }

    $uploaded = 0; $skipped = 0
    Get-ChildItem -Path $GifsSource -Recurse -File | ForEach-Object {
        $relPath = $_.FullName.Substring($GifsSource.Length + 1) -replace '\\', '/'
        $localKB = [math]::Round($_.Length / 1KB, 1)
        $remoteSize = $remoteFiles[$relPath]

        if ($remoteSize -eq $null) {
            Write-Host "  [NEW] $relPath (${localKB} KB)" -ForegroundColor Green
            $remoteDir = "${DeployPath}/BotData/Gifs/" + ($relPath -replace '/[^/]+$', '')
            if ($remoteDir -ne "${DeployPath}/BotData/Gifs/") { ssh "${ServerUser}@${ServerIP}" "mkdir -p '$remoteDir'" }
            scp -q $_.FullName "${ServerUser}@${ServerIP}:${DeployPath}/BotData/Gifs/$relPath"
            $uploaded++
        }
        elseif ($remoteSize -ne $_.Length) {
            $remoteKB = [math]::Round($remoteSize / 1KB, 1)
            Write-Host "  [CHG] $relPath (${remoteKB} → ${localKB} KB)" -ForegroundColor Yellow
            scp -q $_.FullName "${ServerUser}@${ServerIP}:${DeployPath}/BotData/Gifs/$relPath"
            $uploaded++
        }
        else {
            Write-Host "  [=] $relPath" -ForegroundColor DarkGray
            $skipped++
        }
    }
    Write-Host "  表情包: 上传 $uploaded 个, 跳过 $skipped 个" -ForegroundColor Green
} else {
    Write-Host "  表情包目录不存在，跳过" -ForegroundColor DarkGray
}

# ---- Step 2: 创建临时打包目录 ----
Write-Host "[2/8] 准备工程文件..." -ForegroundColor Yellow

$TempDir = Join-Path $env:TEMP "hikari_bot_deploy_$(Get-Date -Format 'yyyyMMddHHmmss')"
New-Item -ItemType Directory -Force -Path $TempDir | Out-Null

$ExcludeDirs = @(".git", ".venv", "__pycache__", ".mypy_cache", ".pytest_cache", ".claude", "BotData", "UserData")
Get-ChildItem -Path $SourceDir -Exclude $ExcludeDirs | Copy-Item -Destination $TempDir -Recurse -Force

Write-Host "  工程文件已打包" -ForegroundColor Green

# ---- Step 3: 上传工程文件 ----
Write-Host "[3/8] 上传工程文件..." -ForegroundColor Yellow
ssh "${ServerUser}@${ServerIP}" "mkdir -p ${DeployPath}"
scp -r "${TempDir}\*" "${ServerUser}@${ServerIP}:${DeployPath}/"
Write-Host "  上传完成" -ForegroundColor Green

# ---- Step 4: 清理临时目录 ----
Write-Host "[4/8] 清理临时文件..." -ForegroundColor Yellow
Remove-Item -Recurse -Force $TempDir
Write-Host "  已清理" -ForegroundColor Green

# ---- Step 5: 检查 uv ----
Write-Host "[5/8] 检查 uv 安装..." -ForegroundColor Yellow
$uvCheck = ssh "${ServerUser}@${ServerIP}" "command -v uv || echo 'NOT_FOUND'"
if ($uvCheck -eq "NOT_FOUND") {
    Write-Host "  uv 未安装，正在安装..." -ForegroundColor Yellow
    ssh "${ServerUser}@${ServerIP}" "curl -LsSf https://astral.sh/uv/install.sh | sh"
    Write-Host "  uv 安装完成" -ForegroundColor Green
} else {
    Write-Host "  uv 已安装: $uvCheck" -ForegroundColor Green
}

# ---- Step 6: uv sync ----
Write-Host "[6/8] 安装 Python 依赖 (uv sync)..." -ForegroundColor Yellow
ssh "${ServerUser}@${ServerIP}" "cd ${DeployPath} && uv sync"
Write-Host "  依赖安装完成" -ForegroundColor Green

# ---- Step 7: systemd ----
Write-Host "[7/8] 安装 systemd 服务..." -ForegroundColor Yellow
ssh "${ServerUser}@${ServerIP}" "cp ${DeployPath}/hikari-bot-neo.service /etc/systemd/system/"
ssh "${ServerUser}@${ServerIP}" "systemctl daemon-reload"
ssh "${ServerUser}@${ServerIP}" "systemctl enable ${ServiceName}"
ssh "${ServerUser}@${ServerIP}" "systemctl restart ${ServiceName}"
Write-Host "  服务已安装并启动" -ForegroundColor Green

# ---- Step 8: 状态 ----
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
