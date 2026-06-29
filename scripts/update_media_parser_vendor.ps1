param(
    [string]$Repo = "https://github.com/drdon1234/astrbot_plugin_media_parser.git",
    [string]$Ref = "main"
)

$ErrorActionPreference = "Stop"

$root = Resolve-Path (Join-Path $PSScriptRoot "..")
$vendor = Join-Path $root "third_party\astrbot_plugin_media_parser"
$tempRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("hikari_media_parser_vendor_" + [guid]::NewGuid().ToString("N"))
$clone = Join-Path $tempRoot "source"

try {
    git clone --depth 1 --branch $Ref $Repo $clone
    if (Test-Path (Join-Path $clone ".git")) {
        Remove-Item -LiteralPath (Join-Path $clone ".git") -Recurse -Force
    }

    if (Test-Path $vendor) {
        Remove-Item -LiteralPath $vendor -Recurse -Force
    }
    New-Item -ItemType Directory -Force (Split-Path $vendor) | Out-Null
    Copy-Item -LiteralPath $clone -Destination $vendor -Recurse

    Write-Host "Updated vendored media parser from $Repo ($Ref)."
    Write-Host "Next: uv run python -m compileall plugins\media_parser third_party\astrbot_plugin_media_parser"
}
finally {
    if (Test-Path $tempRoot) {
        Remove-Item -LiteralPath $tempRoot -Recurse -Force
    }
}
