# Prefer an installed uv; fall back to the project-local copy.
$taskRoot = Split-Path -Parent $PSScriptRoot
if (-not $env:UV_CACHE_DIR) { $env:UV_CACHE_DIR = Join-Path $taskRoot '.uv-cache' }
$taskUv = Get-Command uv -ErrorAction SilentlyContinue
if ($taskUv) {
    & $taskUv.Source @args
} else {
    $taskLocalUv = Join-Path $taskRoot '.tools\bin\uv.exe'
    if (-not (Test-Path -LiteralPath $taskLocalUv)) {
        throw 'uv not found. Install uv or run: python -m pip install --target .tools uv'
    }
    & $taskLocalUv @args
}
exit $LASTEXITCODE
