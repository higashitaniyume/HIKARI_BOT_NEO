# HIKARI_BOT_NEO source-mounted deployment script.
# Usage:
#   .\deploy.ps1        # sync source and restart hikaribot on the server
#   .\deploy.ps1 -l     # start only local hikaribot from the current source tree

param(
    [string]$ServerIP = "192.168.31.2",
    [string]$ServerUser = "root",
    [string]$DeployPath = "/opt/hikaribot-docker",
    [string]$NapcatAccount = "",
    [Alias("l")]
    [switch]$Local,
    [switch]$AllServices
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$ProjectRoot = $PSScriptRoot
$ServerCompose = Join-Path $ProjectRoot "deploy\docker-compose.server.yml"
$LocalCompose = Join-Path $ProjectRoot "docker-compose.yml"
$LegacyDeployPath = "/opt/hikaribot-dockcer"

function Quote-RemoteSingle {
    param([string]$Value)
    return "'" + ($Value -replace "'", "'\''") + "'"
}

function Run-Remote {
    param([string]$Command)
    ssh "${ServerUser}@${ServerIP}" $Command
}

function New-SourceArchive {
    param([string]$ArchivePath)

    $fileListPath = Join-Path $env:TEMP "hikaribot-source-$PID.txt"
    try {
        $files = @(git -C $ProjectRoot ls-files --cached --others --exclude-standard)
        if ($LASTEXITCODE -ne 0 -or $files.Count -eq 0) {
            throw "无法读取要部署的项目文件。"
        }
        [System.IO.File]::WriteAllLines($fileListPath, $files, [System.Text.UTF8Encoding]::new($false))
        & tar.exe -czf $ArchivePath -C $ProjectRoot -T $fileListPath
        if ($LASTEXITCODE -ne 0) {
            throw "创建源码归档失败。"
        }
    } finally {
        Remove-Item -LiteralPath $fileListPath -Force -ErrorAction SilentlyContinue
    }
}

if (-not (Test-Path $LocalCompose)) {
    throw "Missing local compose file: $LocalCompose"
}

if (-not $Local -and -not (Test-Path $ServerCompose)) {
    throw "Missing server compose template: $ServerCompose"
}

Set-Location $ProjectRoot

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  HIKARI BOT NEO - 源码挂载部署" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

if ($Local) {
    $localDirs = @("BotData", "UserData", "sharedFolder", "tmp\hikari_bot")
    foreach ($dir in $localDirs) {
        New-Item -ItemType Directory -Force -Path (Join-Path $ProjectRoot $dir) | Out-Null
    }

    Write-Host "从当前源码目录启动本机 hikaribot（不构建镜像）..." -ForegroundColor Yellow
    docker compose -f $LocalCompose up -d --no-deps hikaribot
    docker compose -f $LocalCompose restart hikaribot

    Write-Host "本地 hikaribot 已启动。" -ForegroundColor Green
    Write-Host "日志: docker compose -f `"$LocalCompose`" logs -f hikaribot" -ForegroundColor Gray
    return
}

$quotedDeployPath = Quote-RemoteSingle $DeployPath
$quotedLegacyPath = Quote-RemoteSingle $LegacyDeployPath
$quotedAppPath = Quote-RemoteSingle "$DeployPath/app"
$quotedStagingPath = Quote-RemoteSingle "$DeployPath/.source-staging"

Write-Host "准备服务器目录..." -ForegroundColor Yellow
if ($DeployPath -eq "/opt/hikaribot-docker") {
    Run-Remote "if [ ! -d $quotedDeployPath ] && [ -d $quotedLegacyPath ]; then cd $quotedLegacyPath && docker compose stop hikaribot || true; mv $quotedLegacyPath $quotedDeployPath; fi"
}
Run-Remote "mkdir -p $quotedAppPath $quotedDeployPath/BotData $quotedDeployPath/UserData $quotedDeployPath/sharedFolder $quotedDeployPath/tmp/hikari_bot $quotedDeployPath/napcat/config $quotedDeployPath/napcat/ntqq $quotedDeployPath/astrbot/data $quotedDeployPath/legacy/pixiv_cache"

$archivePath = Join-Path $env:TEMP "hikaribot-source-$PID.tar.gz"
$remoteArchivePath = "/tmp/hikaribot-source-$PID.tar.gz"
try {
    Write-Host "打包并上传源码..." -ForegroundColor Yellow
    New-SourceArchive $archivePath
    scp $archivePath "${ServerUser}@${ServerIP}:$remoteArchivePath"

    Write-Host "同步源码目录..." -ForegroundColor Yellow
    $quotedRemoteArchivePath = Quote-RemoteSingle $remoteArchivePath
    Run-Remote "test -d $quotedAppPath && mkdir -p $quotedStagingPath && find $quotedStagingPath -mindepth 1 -maxdepth 1 -exec rm -rf -- {} + && tar -xzf $quotedRemoteArchivePath -C $quotedStagingPath && rsync -a --delete $quotedStagingPath/ $quotedAppPath/ && chmod +x $quotedAppPath/install.sh && find $quotedStagingPath -mindepth 1 -maxdepth 1 -exec rm -rf -- {} + && rm -f $quotedRemoteArchivePath"
} finally {
    Remove-Item -LiteralPath $archivePath -Force -ErrorAction SilentlyContinue
}

Write-Host "上传 Docker Compose 配置..." -ForegroundColor Yellow
scp $ServerCompose "${ServerUser}@${ServerIP}:${DeployPath}/docker-compose.yml"

if ($NapcatAccount -ne "") {
    Write-Host "更新 NapCat 账号配置..." -ForegroundColor Yellow
    $quotedEnvPath = Quote-RemoteSingle "$DeployPath/.env"
    $quotedAccountLine = Quote-RemoteSingle "NAPCAT_ACCOUNT=$NapcatAccount"
    Run-Remote "touch $quotedEnvPath && grep -v '^NAPCAT_ACCOUNT=' $quotedEnvPath > $quotedEnvPath.tmp || true; mv $quotedEnvPath.tmp $quotedEnvPath; printf '%s\n' $quotedAccountLine >> $quotedEnvPath"
}

Write-Host "检查 Compose 配置..." -ForegroundColor Yellow
Run-Remote "cd $quotedDeployPath && docker compose config -q"

Write-Host "启动并重启 hikaribot（无需构建项目镜像）..." -ForegroundColor Yellow
if ($AllServices) {
    Run-Remote "cd $quotedDeployPath && docker compose up -d --remove-orphans && docker compose restart hikaribot"
} else {
    Run-Remote "cd $quotedDeployPath && docker compose up -d --no-deps hikaribot && docker compose restart hikaribot"
}

Write-Host ""
Write-Host "部署完成。" -ForegroundColor Green
Write-Host "查看状态: ssh ${ServerUser}@${ServerIP} `"cd $DeployPath && docker compose ps`"" -ForegroundColor Gray
Write-Host "查看日志: ssh ${ServerUser}@${ServerIP} `"cd $DeployPath && docker compose logs -f hikaribot`"" -ForegroundColor Gray
