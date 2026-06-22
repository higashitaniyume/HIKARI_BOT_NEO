# HIKARI_BOT_NEO 一键部署脚本
# 使用方式：在 Windows PowerShell 中运行 .\deploy.ps1
# 前提：已配置 SSH key 免密登录 root@192.168.31.2

param(
    [string]$ServerIP = "192.168.31.2",
    [string]$ServerUser = "root",
    [string]$DeployPath = "/opt/HIKARI_BOT_NEO",
    [string]$ServiceName = "hikari-bot-neo",
    [switch]$u
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$SourceDir = $PSScriptRoot
$PreserveRemoteItems = @("BotData", "UserData", ".venv")
$ExcludeDirs = @(".git", ".venv", "__pycache__", ".mypy_cache", ".pytest_cache", ".claude", "BotData", "UserData")
$ExcludeFiles = @("*.pyc", "*.pyo")

function Join-RemotePath {
    param(
        [string]$Base,
        [string]$Child
    )

    return ($Base.TrimEnd("/") + "/" + $Child.TrimStart("/"))
}

function Quote-RemoteSingle {
    param([string]$Value)
    return "'" + ($Value -replace "'", "'\''") + "'"
}

function Copy-ProjectFiles {
    param(
        [string]$From,
        [string]$To
    )

    $sourceRoot = (Resolve-Path $From).Path
    Get-ChildItem -Path $sourceRoot -Recurse -File -Force | ForEach-Object {
        $relativePath = $_.FullName.Substring($sourceRoot.Length + 1)
        $parts = $relativePath -split '[\\/]'

        $skip = $false
        foreach ($dir in $ExcludeDirs) {
            if ($parts -contains $dir) {
                $skip = $true
                break
            }
        }

        if (-not $skip) {
            foreach ($pattern in $ExcludeFiles) {
                if ($_.Name -like $pattern) {
                    $skip = $true
                    break
                }
            }
        }

        if (-not $skip) {
            $dest = Join-Path $To $relativePath
            $destDir = Split-Path -Parent $dest
            New-Item -ItemType Directory -Force -Path $destDir | Out-Null
            Copy-Item -LiteralPath $_.FullName -Destination $dest -Force
        }
    }
}

function Reset-RemoteProjectFiles {
    Write-Host "[3/9] 清理远端旧工程文件..." -ForegroundColor Yellow

    $quotedDeployPath = Quote-RemoteSingle $DeployPath
    $keepArgs = ($PreserveRemoteItems | ForEach-Object {
        "! -name " + (Quote-RemoteSingle $_)
    }) -join " "

    ssh "${ServerUser}@${ServerIP}" "mkdir -p $quotedDeployPath && find $quotedDeployPath -mindepth 1 -maxdepth 1 $keepArgs -exec rm -rf -- {} +"
    Write-Host "  已清理远端工程文件，保留 BotData/UserData/.venv" -ForegroundColor Green
}

# ---- 增量上传表情包（公共函数）----
function Sync-Gifs {
    Write-Host "[Gifs] 增量同步表情包..." -ForegroundColor Yellow
    $GifsSource = Join-Path $SourceDir "BotData\Gifs"
    if (-not (Test-Path $GifsSource)) {
        Write-Host "  表情包目录不存在，跳过" -ForegroundColor DarkGray
        return
    }

    $remoteGifsPath = Join-RemotePath $DeployPath "BotData/Gifs"
    $quotedRemoteGifsPath = Quote-RemoteSingle $remoteGifsPath
    ssh "${ServerUser}@${ServerIP}" "mkdir -p $quotedRemoteGifsPath"

    $escapedPrefix = [regex]::Escape($remoteGifsPath.TrimEnd("/") + "/")
    $remoteFiles = @{}
    $remoteList = ssh "${ServerUser}@${ServerIP}" "find $quotedRemoteGifsPath -type f -exec stat -c '%n|%s' {} \; 2>/dev/null"
    if ($remoteList) {
        $remoteList -split "`n" | ForEach-Object {
            if ($_ -match '\|(\d+)$') {
                $rp = $_ -replace "^$escapedPrefix", "" -replace '\|\d+$', ''
                $remoteFiles[$rp] = [int64]($matches[1])
            }
        }
    }

    $uploaded = 0; $skipped = 0
    Get-ChildItem -Path $GifsSource -Recurse -File | ForEach-Object {
        $relPath = $_.FullName.Substring($GifsSource.Length + 1) -replace '\\', '/'
        $localKB = [math]::Round($_.Length / 1KB, 1)
        $remoteSize = $remoteFiles[$relPath]

        if ($null -eq $remoteSize) {
            Write-Host "  [NEW] $relPath (${localKB} KB)" -ForegroundColor Green
            if ($relPath.Contains("/")) {
                $remoteDirPart = $relPath -replace '/[^/]+$', ''
                $remoteDir = Join-RemotePath $remoteGifsPath $remoteDirPart
                ssh "${ServerUser}@${ServerIP}" "mkdir -p $(Quote-RemoteSingle $remoteDir)"
            }
            scp -q $_.FullName "${ServerUser}@${ServerIP}:${remoteGifsPath}/$relPath"
            $uploaded++
        }
        elseif ($remoteSize -ne $_.Length) {
            $remoteKB = [math]::Round($remoteSize / 1KB, 1)
            Write-Host "  [CHG] $relPath (${remoteKB} → ${localKB} KB)" -ForegroundColor Yellow
            scp -q $_.FullName "${ServerUser}@${ServerIP}:${remoteGifsPath}/$relPath"
            $uploaded++
        }
        else {
            Write-Host "  [=] $relPath" -ForegroundColor DarkGray
            $skipped++
        }
    }
    Write-Host "  表情包: 上传 $uploaded 个, 跳过 $skipped 个" -ForegroundColor Green
}

# -u 模式：只上传表情包，其他啥都不干
if ($u) {
    Write-Host "========================================" -ForegroundColor Cyan
    Write-Host "  HIKARI BOT NEO - 仅同步表情包" -ForegroundColor Cyan
    Write-Host "========================================" -ForegroundColor Cyan
    Write-Host ""
    Sync-Gifs
    Write-Host ""
    Write-Host "完成。" -ForegroundColor Green
    exit 0
}

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  HIKARI BOT NEO - 一键部署" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# ---- Step 1: 增量上传表情包 ----
Sync-Gifs

# ---- Step 2: 创建临时打包目录 ----
Write-Host "[2/9] 准备工程文件..." -ForegroundColor Yellow

$TempDir = Join-Path $env:TEMP "hikari_bot_deploy_$(Get-Date -Format 'yyyyMMddHHmmss')"
New-Item -ItemType Directory -Force -Path $TempDir | Out-Null

Copy-ProjectFiles -From $SourceDir -To $TempDir

Write-Host "  工程文件已打包" -ForegroundColor Green

# ---- Step 3: 清理远端旧工程文件 ----
Reset-RemoteProjectFiles

# ---- Step 4: 上传工程文件 ----
Write-Host "[4/9] 上传工程文件..." -ForegroundColor Yellow
ssh "${ServerUser}@${ServerIP}" "mkdir -p $(Quote-RemoteSingle $DeployPath)"
scp -r "${TempDir}\*" "${ServerUser}@${ServerIP}:${DeployPath}/"
Write-Host "  上传完成" -ForegroundColor Green

# ---- Step 5: 清理临时目录 ----
Write-Host "[5/9] 清理临时文件..." -ForegroundColor Yellow
Remove-Item -Recurse -Force $TempDir
Write-Host "  已清理" -ForegroundColor Green

# ---- Step 6: 检查 uv ----
Write-Host "[6/9] 检查 uv 安装..." -ForegroundColor Yellow
$uvCheck = ssh "${ServerUser}@${ServerIP}" "command -v uv || echo 'NOT_FOUND'"
if ($uvCheck -eq "NOT_FOUND") {
    Write-Host "  uv 未安装，正在安装..." -ForegroundColor Yellow
    ssh "${ServerUser}@${ServerIP}" "curl -LsSf https://astral.sh/uv/install.sh | sh"
    Write-Host "  uv 安装完成" -ForegroundColor Green
} else {
    Write-Host "  uv 已安装: $uvCheck" -ForegroundColor Green
}

# ---- Step 7: uv sync ----
Write-Host "[7/9] 安装 Python 依赖 (uv sync)..." -ForegroundColor Yellow
ssh "${ServerUser}@${ServerIP}" "cd $(Quote-RemoteSingle $DeployPath) && uv sync"
Write-Host "  依赖安装完成" -ForegroundColor Green

# ---- Step 8: systemd ----
Write-Host "[8/9] 安装 systemd 服务..." -ForegroundColor Yellow
$serviceFile = Quote-RemoteSingle (Join-RemotePath $DeployPath "hikari-bot-neo.service")
ssh "${ServerUser}@${ServerIP}" "cp $serviceFile /etc/systemd/system/"
ssh "${ServerUser}@${ServerIP}" "systemctl daemon-reload"
ssh "${ServerUser}@${ServerIP}" "systemctl enable ${ServiceName}"
ssh "${ServerUser}@${ServerIP}" "systemctl restart ${ServiceName}"
Write-Host "  服务已安装并启动" -ForegroundColor Green

# ---- Step 9: 状态 ----
Write-Host "[9/9] 检查服务状态..." -ForegroundColor Yellow
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
