$ErrorActionPreference = "Stop"

$taskName = "FerniqTelegramBot"
$repoDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$launcherPath = Join-Path $repoDir "arrancar_bot_automatico.ps1"
$currentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name

if (-not (Test-Path $launcherPath)) {
    throw "No existe el lanzador: $launcherPath"
}

$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$launcherPath`""

$trigger = New-ScheduledTaskTrigger -AtLogOn -User $currentUser
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew

Register-ScheduledTask `
    -TaskName $taskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Description "Arranca automaticamente el bot de Telegram de Ferniq al iniciar sesion." `
    -Force | Out-Null

Write-Output "Tarea programada creada: $taskName"
Write-Output "Usuario: $currentUser"
Write-Output "Lanzador: $launcherPath"
