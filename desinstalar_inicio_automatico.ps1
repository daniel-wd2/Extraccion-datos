$ErrorActionPreference = "Stop"

$taskName = "FerniqTelegramBot"

if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    Write-Output "Tarea eliminada: $taskName"
} else {
    Write-Output "La tarea no existe: $taskName"
}
