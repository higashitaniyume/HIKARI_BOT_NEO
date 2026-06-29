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

function Get-SourceRelativePaths {
    $files = @(git -C $ProjectRoot ls-files --cached --others --exclude-standard)
    if ($LASTEXITCODE -ne 0 -or $files.Count -eq 0) {
        throw "无法读取要部署的项目文件。"
    }

    foreach ($relativePath in $files) {
        $localPath = Join-Path $ProjectRoot $relativePath
        if (-not (Test-Path -LiteralPath $localPath -PathType Leaf)) {
            # 已删除但仍在 Git 索引里的文件无需打包；同步阶段会由 rsync --delete 清理远端副本。
            Write-Verbose "跳过已删除文件：$relativePath"
            continue
        }
        $relativePath.Replace("\", "/")
    }
}

function New-SourceArchive {
    param(
        [string[]]$RelativePaths,
        [string]$ArchivePath,
        [string]$ListPath
    )

    if (-not (Get-Command 7z -ErrorAction SilentlyContinue)) {
        throw "未找到 7z 命令。请先安装 7-Zip，并确认 7z 在 PATH 中。"
    }

    $RelativePaths | Set-Content -LiteralPath $ListPath -Encoding UTF8
    Push-Location $ProjectRoot
    try {
        $listFileName = Split-Path -Leaf $ListPath
        7z a -t7z -mx=5 -scsUTF-8 $ArchivePath "@$listFileName" | Out-Host
        if ($LASTEXITCODE -ne 0) {
            throw "7z 打包源码失败。"
        }
    } finally {
        Pop-Location
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
    $localRuntimeRoot = Join-Path $ProjectRoot "runtime"
    $legacySharedPath = Join-Path $ProjectRoot "sharedFolder"
    $runtimeSharedPath = Join-Path $localRuntimeRoot "shared"
    $legacyTmpPath = Join-Path $ProjectRoot "tmp"
    $runtimeTmpPath = Join-Path $localRuntimeRoot "tmp"
    if ((Test-Path $legacySharedPath) -and -not (Test-Path $runtimeSharedPath)) {
        New-Item -ItemType Directory -Force -Path $runtimeSharedPath | Out-Null
        Get-ChildItem -Force $legacySharedPath | Where-Object Name -ne ".gitkeep" | Move-Item -Destination $runtimeSharedPath
    }
    if ((Test-Path $legacyTmpPath) -and -not (Test-Path $runtimeTmpPath)) {
        New-Item -ItemType Directory -Force -Path $localRuntimeRoot | Out-Null
        Move-Item -LiteralPath $legacyTmpPath -Destination $runtimeTmpPath
    }
    $localDirs = @("BotData", "UserData", "runtime\shared", "runtime\tmp\hikari_bot")
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
$quotedRuntimePath = Quote-RemoteSingle "$DeployPath/runtime"
$quotedLegacySharedPath = Quote-RemoteSingle "$DeployPath/sharedFolder"
$quotedRuntimeSharedPath = Quote-RemoteSingle "$DeployPath/runtime/shared"
$quotedLegacyTmpPath = Quote-RemoteSingle "$DeployPath/tmp"
$quotedRuntimeTmpPath = Quote-RemoteSingle "$DeployPath/runtime/tmp"

Write-Host "准备服务器目录..." -ForegroundColor Yellow
if ($DeployPath -eq "/opt/hikaribot-docker") {
    Run-Remote "if [ ! -d $quotedDeployPath ] && [ -d $quotedLegacyPath ]; then cd $quotedLegacyPath && docker compose stop hikaribot || true; mv $quotedLegacyPath $quotedDeployPath; fi"
}
Run-Remote "if [ -d $quotedLegacySharedPath ] && [ ! -e $quotedRuntimeSharedPath ]; then mkdir -p $quotedRuntimePath && mv $quotedLegacySharedPath $quotedRuntimeSharedPath; fi; if [ -d $quotedLegacyTmpPath ] && [ ! -e $quotedRuntimeTmpPath ]; then mkdir -p $quotedRuntimePath && mv $quotedLegacyTmpPath $quotedRuntimeTmpPath; fi; mkdir -p $quotedAppPath $quotedDeployPath/BotData $quotedDeployPath/UserData $quotedRuntimeSharedPath $quotedRuntimeTmpPath/hikari_bot $quotedDeployPath/napcat/config $quotedDeployPath/napcat/ntqq $quotedDeployPath/searxng/core-config $quotedDeployPath/legacy/pixiv_cache"

Write-Host "打包源码..." -ForegroundColor Yellow
$sourcePaths = @(Get-SourceRelativePaths)
$deployArchiveId = [System.Guid]::NewGuid().ToString("N")
$tempRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("hikari-deploy-" + $deployArchiveId)
$archivePath = Join-Path $tempRoot "source.7z"
$listPath = Join-Path $ProjectRoot "deploy-source-files-$deployArchiveId.txt"
$remoteArchivePath = "$DeployPath/.source-staging/source.7z"
$quotedRemoteArchivePath = Quote-RemoteSingle $remoteArchivePath
New-Item -ItemType Directory -Force -Path $tempRoot | Out-Null
try {
    New-SourceArchive -RelativePaths $sourcePaths -ArchivePath $archivePath -ListPath $listPath

    Write-Host "上传源码压缩包..." -ForegroundColor Yellow
    Run-Remote "command -v 7z >/dev/null 2>&1 || { echo '服务器未找到 7z 命令，请先安装 p7zip/7zip。' >&2; exit 127; }; mkdir -p $quotedStagingPath && find $quotedStagingPath -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +"
    scp -- $archivePath "${ServerUser}@${ServerIP}:$remoteArchivePath"
    if ($LASTEXITCODE -ne 0) {
        throw "上传源码压缩包失败。"
    }

    Write-Host "解压并同步源码目录..." -ForegroundColor Yellow
    Run-Remote "7z x -y $quotedRemoteArchivePath -o$quotedStagingPath >/dev/null && rm -f $quotedRemoteArchivePath && rsync -a --delete $quotedStagingPath/ $quotedAppPath/ && chmod +x $quotedAppPath/install.sh && cp $quotedAppPath/deploy/docker-compose.server.yml $quotedDeployPath/docker-compose.yml && find $quotedStagingPath -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +"
} finally {
    Remove-Item -LiteralPath $listPath -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction SilentlyContinue
}

$quotedSearxngSettingsPath = Quote-RemoteSingle "$DeployPath/searxng/core-config/settings.yml"
$quotedSearxngTemplatePath = Quote-RemoteSingle "$DeployPath/app/deploy/searxng/core-config/settings.yml"
Run-Remote "if [ ! -f $quotedSearxngSettingsPath ]; then cp $quotedSearxngTemplatePath $quotedSearxngSettingsPath && secret=`$(openssl rand -hex 32 2>/dev/null || date +%s) && sed -i `"s/__SEARXNG_SECRET__/`$secret/g`" $quotedSearxngSettingsPath; fi"

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
    Run-Remote "cd $quotedDeployPath && docker compose up -d --no-deps hikaribot napcat cobalt searxng searxng-valkey --remove-orphans && docker compose restart hikaribot"
}

Write-Host ""
Write-Host "部署完成。" -ForegroundColor Green
Write-Host "查看状态: ssh ${ServerUser}@${ServerIP} `"cd $DeployPath && docker compose ps`"" -ForegroundColor Gray
Write-Host "查看日志: ssh ${ServerUser}@${ServerIP} `"cd $DeployPath && docker compose logs -f hikaribot`"" -ForegroundColor Gray
