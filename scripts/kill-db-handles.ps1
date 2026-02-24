# Kill processes holding dangling handles to the pixel-hawk database.
# Requires handle.exe (Sysinternals) on PATH or in the same directory.

$db = "pixel-hawk.db"

$output = handle.exe $db -accepteula -nobanner 2>$null
if ($LASTEXITCODE -ne 0 -or -not $output) {
    Write-Host "No handles found for $db"
    exit 0
}

$pids = $output |
    Select-String "pid:\s+(\d+)" |
    ForEach-Object { [int]$_.Matches[0].Groups[1].Value } |
    Select-Object -Unique

if (-not $pids) {
    Write-Host "No handles found for $db"
    exit 0
}

foreach ($id in $pids) {
    $proc = Get-Process -Id $id -ErrorAction SilentlyContinue
    if ($proc) {
        Write-Host "Killing $($proc.ProcessName) (PID $id)"
        Stop-Process -Id $id -Force
    }
}
