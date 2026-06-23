param(
    [string[]]$Profile = @()
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

$createdConfig = $false

if (-not (Test-Path "BotData/config.json")) {
    Copy-Item "BotData/config.example.json" "BotData/config.json"
    Write-Host "Created BotData/config.json from example."
    $createdConfig = $true
}

$pluginExamples = @(
    "pixiv_parser",
    "cobalt_parser",
    "media_transcoder",
    "sticker_web"
)

foreach ($name in $pluginExamples) {
    $example = "BotData/plugin_configs/$name.example.json"
    $target = "BotData/plugin_configs/$name.json"
    if ((Test-Path $example) -and -not (Test-Path $target)) {
        Copy-Item $example $target
        Write-Host "Created $target from example."
        $createdConfig = $true
    }
}

if ($createdConfig) {
    Write-Host "Please edit BotData/config.json and BotData/plugin_configs/*.json, then run this script again."
    exit 1
}

if (-not (Test-Path "docker-compose.yml")) {
    throw "docker-compose.yml not found."
}

$composeArgs = @("compose")
foreach ($item in $Profile) {
    $composeArgs += @("--profile", $item)
}
$composeArgs += @("up", "-d", "--build", "--remove-orphans")

docker @composeArgs

Write-Host "Docker compose deployment finished."
Write-Host "Logs: docker compose logs -f hikari-bot-neo"
