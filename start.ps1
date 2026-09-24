param (
    [int]$Port = 9090,
    [string]$HostName = "0.0.0.0"
)

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptDir

Write-Host "Starting System Monitor on http://localhost:$Port" -ForegroundColor Cyan
python app.py --port $Port --host $HostName
