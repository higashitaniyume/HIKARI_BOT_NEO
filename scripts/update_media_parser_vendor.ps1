param(
    [string]$Repo = "https://github.com/drdon1234/astrbot_plugin_media_parser.git",
    [string]$Ref = "main"
)

$ErrorActionPreference = "Stop"

$root = Resolve-Path (Join-Path $PSScriptRoot "..")
$vendor = Join-Path $root "third_party\astrbot_plugin_media_parser"
$tempRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("hikari_media_parser_vendor_" + [guid]::NewGuid().ToString("N"))
$clone = Join-Path $tempRoot "source"

function Remove-PixivFromVendor {
    # 本仓库用 plugins/pixiv_parser 解析 Pixiv，vendored 副本不保留上游的 Pixiv 解析。
    # 摘除后必须通过下方校验；若上游改动导致正则失配，脚本会报错并要求手动摘除。
    $pixivFile = Join-Path $vendor "core\parser\platform\pixiv.py"
    if (Test-Path $pixivFile) {
        Remove-Item -LiteralPath $pixivFile -Force
    }

    $platformInit = Join-Path $vendor "core\parser\platform\__init__.py"
    if (Test-Path $platformInit) {
        # 上游文件是无 BOM 的 UTF-8；必须显式按 UTF-8 读写，避免被系统代码页破坏。
        $utf8 = [System.Text.Encoding]::UTF8
        $noBom = New-Object System.Text.UTF8Encoding $false
        $lines = [System.IO.File]::ReadAllLines($platformInit, $utf8) |
            Where-Object { $_ -notmatch 'Pixiv' }
        [System.IO.File]::WriteAllLines($platformInit, $lines, $noBom)
    }

    $configManager = Join-Path $vendor "core\config_manager.py"
    if (Test-Path $configManager) {
        $text = [System.IO.File]::ReadAllText($configManager, [System.Text.Encoding]::UTF8)
        $text = $text -replace '(?m)^\s*PixivParser,\r?\n', ''
        $text = $text -replace '(?m)^\s*"pixiv",\r?\n', ''
        $text = $text -replace '(?m)^\s*pixiv_use_proxy: bool = False\r?\n', ''
        $text = $text -replace '(?ms)^@dataclass\s*\r?\nclass PixivConfig:\s*\r?\n.*?\r?\n\s*\r?\n(?=@dataclass)', ''
        $text = $text -replace '(?m)^\s*self\._enable_pixiv = self\._parser_enabled\("pixiv"\)\r?\n', ''
        $text = $text -replace '(?ms)^        # --- pixiv ---\r?\n.*?\r?\n\r?\n(?=        # --- proxy ---)', ''
        $text = $text -replace '(?ms)^            pixiv_use_proxy=self\._parse_bool\(\r?\n.*?\r?\n            \),\r?\n', ''
        $text = $text -replace '(?ms)^        if self\._enable_pixiv:\r?\n.*?\r?\n\r?\n(?=\s*return parsers)', ''
        [System.IO.File]::WriteAllText($configManager, $text, (New-Object System.Text.UTF8Encoding $false))
    }

    $schema = Join-Path $vendor "_conf_schema.json"
    if (Test-Path $schema) {
        $text = [System.IO.File]::ReadAllText($schema, [System.Text.Encoding]::UTF8)
        $text = $text -replace '(?ms)^            "pixiv": \{\r?\n.*?\r?\n            \},\r?\n', ''
        $text = $text -replace '(?ms)^    "pixiv": \{\r?\n.*?\r?\n    \},\r?\n', ''
        [System.IO.File]::WriteAllText($schema, $text, (New-Object System.Text.UTF8Encoding $false))
    }

    $leftover = Get-ChildItem $vendor -Recurse -Include *.py, *.json |
        Select-String -Pattern "pixiv" -SimpleMatch
    if ($leftover) {
        Write-Host ""
        Write-Host "ERROR: Pixiv references remain after stripping:" -ForegroundColor Red
        $leftover | ForEach-Object { Write-Host "  $($_.Path):$($_.LineNumber): $($_.Line.Trim())" }
        Write-Host "Strip them manually, then re-run the verification grep." -ForegroundColor Red
        exit 1
    }
}

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
    Get-ChildItem $vendor -Recurse -Directory -Filter "__pycache__" |
        Remove-Item -Recurse -Force

    Remove-PixivFromVendor

    Write-Host "Updated vendored media parser from $Repo ($Ref), Pixiv parser stripped."
    Write-Host "Next: uv run python -m compileall plugins\media_parser third_party\astrbot_plugin_media_parser"
}
finally {
    if (Test-Path $tempRoot) {
        Remove-Item -LiteralPath $tempRoot -Recurse -Force
    }
}
