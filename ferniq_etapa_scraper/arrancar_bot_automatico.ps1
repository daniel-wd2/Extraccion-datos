$ErrorActionPreference = "Stop"

$repoDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$botScript = Join-Path $repoDir "telegram_bot.py"
$stdoutLog = Join-Path $repoDir "telegram_bot_stdout_autostart.log"
$stderrLog = Join-Path $repoDir "telegram_bot_stderr_autostart.log"

$existingBot = Get-CimInstance Win32_Process |
    Where-Object { $_.Name -eq "python.exe" -and $_.CommandLine -like "*telegram_bot.py*" } |
    Select-Object -First 1

if ($existingBot) {
    Write-Output "El bot ya esta corriendo con PID $($existingBot.ProcessId)."
    exit 0
}

$pythonCommand = Get-Command python -ErrorAction Stop
$pythonPath = $pythonCommand.Source

Start-Process `
    -FilePath $pythonPath `
    -ArgumentList $botScript `
    -WorkingDirectory $repoDir `
    -WindowStyle Hidden `
    -RedirectStandardOutput $stdoutLog `
    -RedirectStandardError $stderrLog

Write-Output "Bot lanzado en segundo plano."
