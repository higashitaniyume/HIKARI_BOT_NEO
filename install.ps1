# One-command install and update for a source-mounted HIKARI BOT NEO deployment.
#
# First install:
#   irm https://raw.githubusercontent.com/higashitaniyume/HIKARI_BOT_NEO/main/install.ps1 | iex
#
# Later updates:
#   & "$HOME/hikaribot-docker/app/install.ps1"

[CmdletBinding()]
param(
    [string]$RepositoryUrl = $(if ($env:HIKARI_REPOSITORY_URL) { $env:HIKARI_REPOSITORY_URL } else { "https://github.com/higashitaniyume/HIKARI_BOT_NEO.git" }),
    [string]$DeployPath = $(if ($env:HIKARI_DEPLOY_DIR) { $env:HIKARI_DEPLOY_DIR } elseif ($env:OS -eq "Windows_NT") { Join-Path $HOME "hikaribot-docker" } else { "/opt/hikaribot-docker" }),
    [string]$Branch = $(if ($env:HIKARI_BRANCH) { $env:HIKARI_BRANCH } else { "main" })
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$AppPath = Join-Path $DeployPath "app"
$ComposePath = Join-Path $DeployPath "docker-compose.yml"
$EnvPath = Join-Path $DeployPath ".env"

function Require-Command {
    param([string]$Name)
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "缺少命令：$Name"
    }
}

Require-Command git
Require-Command docker

& docker compose version | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "未找到 Docker Compose v2 插件，请先安装 Docker Desktop 或 docker-compose-plugin。"
}

if (Test-Path (Join-Path $AppPath ".git")) {
    $sourceStatus = & git -C $AppPath status --porcelain
    if ($LASTEXITCODE -ne 0) {
        throw "无法检查源码目录：$AppPath"
    }
    if ($sourceStatus) {
        throw "检测到 $AppPath 有未提交或未跟踪的源码修改，已停止更新以避免覆盖。"
    }

    Write-Host "更新机器人源码（$Branch）..." -ForegroundColor Yellow
    & git -C $AppPath fetch --depth 1 origin $Branch
    & git -C $AppPath checkout --quiet $Branch
    & git -C $AppPath reset --hard "origin/$Branch"
} else {
    if ((Test-Path $AppPath) -and (Get-ChildItem -Force $AppPath | Select-Object -First 1)) {
        throw "$AppPath 已存在但不是 Git 仓库；请先迁移或清理该目录。"
    }

    Write-Host "拉取机器人源码（$Branch）..." -ForegroundColor Yellow
    New-Item -ItemType Directory -Force -Path $DeployPath | Out-Null
    & git clone --depth 1 --branch $Branch $RepositoryUrl $AppPath
}

$runtimeRoot = Join-Path $DeployPath "runtime"
$legacySharedPath = Join-Path $DeployPath "sharedFolder"
$runtimeSharedPath = Join-Path $runtimeRoot "shared"
$legacyTmpPath = Join-Path $DeployPath "tmp"
$runtimeTmpPath = Join-Path $runtimeRoot "tmp"
if ((Test-Path $legacySharedPath) -and -not (Test-Path $runtimeSharedPath)) {
    New-Item -ItemType Directory -Force -Path $runtimeRoot | Out-Null
    Move-Item -LiteralPath $legacySharedPath -Destination $runtimeSharedPath
}
if ((Test-Path $legacyTmpPath) -and -not (Test-Path $runtimeTmpPath)) {
    New-Item -ItemType Directory -Force -Path $runtimeRoot | Out-Null
    Move-Item -LiteralPath $legacyTmpPath -Destination $runtimeTmpPath
}

$runtimeDirs = @(
    "BotData",
    "UserData",
    "runtime/shared",
    "runtime/tmp/hikari_bot",
    "napcat/config",
    "napcat/ntqq",
    "astrbot/data",
    "legacy/pixiv_cache"
)
foreach ($dir in $runtimeDirs) {
    New-Item -ItemType Directory -Force -Path (Join-Path $DeployPath $dir) | Out-Null
}

Copy-Item -Force (Join-Path $AppPath "deploy/docker-compose.server.yml") $ComposePath
if (-not (Test-Path $EnvPath)) {
    Copy-Item (Join-Path $AppPath ".env.example") $EnvPath
    Write-Host "已创建 $EnvPath，可按需填写 NAPCAT_ACCOUNT 和端口设置。" -ForegroundColor Yellow
}

Write-Host "检查 Docker Compose 配置..." -ForegroundColor Yellow
& docker compose --project-directory $DeployPath -f $ComposePath config -q

Write-Host "启动服务..." -ForegroundColor Yellow
& docker compose --project-directory $DeployPath -f $ComposePath up -d --remove-orphans
& docker compose --project-directory $DeployPath -f $ComposePath restart hikaribot

Write-Host ""
Write-Host "部署完成。" -ForegroundColor Green
Write-Host "运行配置：$DeployPath/BotData/"
Write-Host "首次启动后请编辑 BotData/config.json 与 BotData/plugin_configs/*.json。"
Write-Host "查看日志：docker compose --project-directory `"$DeployPath`" logs -f hikaribot"
Write-Host "以后更新：& `"$AppPath/install.ps1`""
