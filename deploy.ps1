# HIKARI_BOT_NEO Docker server deploy script.
# Usage: .\deploy.ps1
# Default target: root@192.168.31.2:/opt/hikaribot-dockcer

param(
    [string]$ServerIP = "192.168.31.2",
    [string]$ServerUser = "root",
    [string]$DeployPath = "/opt/hikaribot-dockcer",
    [string]$Image = "higashitaniyume/hikaribot:latest",
    [string]$NapcatAccount = "",
    [switch]$Push,
    [switch]$SkipBuild
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$ProjectRoot = $PSScriptRoot
$ServerCompose = Join-Path $ProjectRoot "deploy\docker-compose.server.yml"

function Quote-RemoteSingle {
    param([string]$Value)
    return "'" + ($Value -replace "'", "'\''") + "'"
}

function Run-Remote {
    param([string]$Command)
    ssh "${ServerUser}@${ServerIP}" $Command
}

if (-not (Test-Path $ServerCompose)) {
    throw "Missing server compose template: $ServerCompose"
}

Set-Location $ProjectRoot

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  HIKARI BOT NEO - Docker 部署" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

if (-not $SkipBuild) {
    Write-Host "[1/6] 构建镜像 $Image ..." -ForegroundColor Yellow
    docker build -t $Image .
} else {
    Write-Host "[1/6] 跳过本地构建，使用已有镜像 $Image" -ForegroundColor Yellow
}

if ($Push) {
    Write-Host "[2/6] 推送镜像到镜像仓库..." -ForegroundColor Yellow
    docker push $Image
} else {
    Write-Host "[2/6] 通过 SSH 传输镜像到服务器..." -ForegroundColor Yellow
    $safeImageName = ($Image -replace '[^A-Za-z0-9_.-]', '_')
    $TarPath = Join-Path $env:TEMP "$safeImageName.tar"
    docker save $Image -o $TarPath
    scp $TarPath "${ServerUser}@${ServerIP}:/tmp/$safeImageName.tar"
    Remove-Item -LiteralPath $TarPath -Force
    Run-Remote "docker load -i /tmp/$safeImageName.tar && rm -f /tmp/$safeImageName.tar"
}

Write-Host "[3/6] 准备服务器部署目录..." -ForegroundColor Yellow
$quotedDeployPath = Quote-RemoteSingle $DeployPath
Run-Remote "mkdir -p $quotedDeployPath/BotData $quotedDeployPath/UserData $quotedDeployPath/sharedFolder $quotedDeployPath/tmp/hikari_bot $quotedDeployPath/napcat/config $quotedDeployPath/napcat/ntqq $quotedDeployPath/astrbot/data $quotedDeployPath/legacy/pixiv_cache"

Write-Host "[4/6] 上传 Docker Compose 文件..." -ForegroundColor Yellow
scp $ServerCompose "${ServerUser}@${ServerIP}:${DeployPath}/docker-compose.yml"

Write-Host "[5/6] 写入部署环境变量..." -ForegroundColor Yellow
$envFile = "$quotedDeployPath/.env"
$imageLine = Quote-RemoteSingle "HIKARI_IMAGE=$Image"
Run-Remote "touch $envFile && grep -v '^HIKARI_IMAGE=' $envFile > $envFile.tmp || true && mv $envFile.tmp $envFile && printf '%s\n' $imageLine >> $envFile"

if ($NapcatAccount -ne "") {
    $accountLine = Quote-RemoteSingle "NAPCAT_ACCOUNT=$NapcatAccount"
    Run-Remote "grep -v '^NAPCAT_ACCOUNT=' $envFile > $envFile.tmp || true && mv $envFile.tmp $envFile && printf '%s\n' $accountLine >> $envFile"
}

Write-Host "[6/6] 启动 Docker 服务..." -ForegroundColor Yellow
Run-Remote "docker rm -f hikaribot-docker napcat cobalt astrbot 2>/dev/null || true"
Run-Remote "cd $quotedDeployPath && docker compose up -d --remove-orphans"

Write-Host ""
Write-Host "部署完成。" -ForegroundColor Green
Write-Host "查看状态: ssh ${ServerUser}@${ServerIP} `"cd $DeployPath && docker compose ps`"" -ForegroundColor Gray
Write-Host "查看日志: ssh ${ServerUser}@${ServerIP} `"cd $DeployPath && docker compose logs -f hikaribot`"" -ForegroundColor Gray
