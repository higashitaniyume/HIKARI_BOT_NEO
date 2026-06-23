$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

$createdConfig = $false

if (-not (Test-Path "BotData/config.json")) {
    Copy-Item "BotData/config.example.json" "BotData/config.json"
    Write-Host "Created BotData/config.json from example."
    $createdConfig = $true
}

Get-ChildItem "BotData/plugin_configs" -Filter "*.example.json" | ForEach-Object {
    $name = $_.BaseName -replace "\.example$", ""
    $example = $_.FullName
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

docker compose up -d --build --remove-orphans

Write-Host "Docker compose deployment finished."
Write-Host "Logs: docker compose logs -f hikaribot"
