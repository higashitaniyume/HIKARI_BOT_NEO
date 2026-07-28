# Update music_genre_wiki data.json from upstream source
# Usage: .\scripts\update_music_genre_data.ps1 [-FromFile <path>]
#   -FromFile: optional local path to index.html (for testing without fetching)

param(
    [string]$FromFile = ""
)

$DataDir = Join-Path $PSScriptRoot ".." "plugins" "music_genre_wiki"
$DataFile = Join-Path $DataDir "data.json"
$ExtractScript = Join-Path $PSScriptRoot "extract_music_genre_data.mjs"

if (-not (Test-Path $ExtractScript)) {
    Write-Error "提取脚本不存在: $ExtractScript"
    exit 1
}

if ($FromFile -and (Test-Path $FromFile)) {
    $HtmlFile = $FromFile
    Write-Host "使用本地文件: $HtmlFile"
} else {
    # Download from GitHub
    $RepoUrl = "https://raw.githubusercontent.com/YeisuQwQ/music_genre/main/index.html"
    $HtmlFile = Join-Path $env:TEMP "music_genre_index.html"
    Write-Host "正在从 GitHub 下载最新数据..."
    try {
        Invoke-WebRequest -Uri $RepoUrl -OutFile $HtmlFile -UseBasicParsing
        Write-Host "下载完成"
    } catch {
        Write-Error "下载失败: $_"
        exit 1
    }
}

Write-Host "正在提取数据..."
node $ExtractScript $HtmlFile $DataFile
if ($LASTEXITCODE -ne 0) {
    Write-Error "数据提取失败"
    exit 1
}

Write-Host "数据已更新: $DataFile"
