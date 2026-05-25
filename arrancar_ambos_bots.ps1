param(
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$oportunidadDir = Join-Path $repoRoot "ferniq_oportunidad_scraper"
foreach ($path in @($oportunidadDir)) {
    if (-not (Test-Path -LiteralPath $path -PathType Container)) {
        throw "No existe la carpeta requerida: $path"
    }
}

function Get-PythonCommand {
    foreach ($candidate in @("py", "python")) {
        if (Get-Command $candidate -ErrorAction SilentlyContinue) {
            return $candidate
        }
    }

    throw "No se encontro Python. Instala Python 3 y vuelve a ejecutar este script."
}

function Start-BotWindow {
    param(
        [Parameter(Mandatory = $true)]
        [string]$BotName,
        [Parameter(Mandatory = $true)]
        [string]$WorkingDirectory,
        [Parameter(Mandatory = $true)]
        [string]$PythonCommand,
        [Parameter(Mandatory = $true)]
        [string]$ScriptName
    )

    $escapedDir = $WorkingDirectory.Replace("'", "''")
    $escapedScript = $ScriptName.Replace("'", "''")
    $escapedPython = $PythonCommand.Replace("'", "''")

    $command = @"
Set-Location -LiteralPath '$escapedDir'
& '$escapedPython' '$escapedScript'
if (`$LASTEXITCODE -ne 0) {
    Write-Host ''
    Write-Host 'El proceso $BotName se cerro con error.' -ForegroundColor Red
}
Read-Host 'Pulsa ENTER para cerrar esta ventana'
"@

    if ($DryRun) {
        Write-Host "[$BotName]"
        Write-Host $command
        Write-Host ""
        return
    }

    Start-Process powershell -ArgumentList @(
        "-NoExit",
        "-Command",
        $command
    ) | Out-Null
}

function Test-BotAlreadyRunning {
    param(
        [Parameter(Mandatory = $true)]
        [string]$WorkingDirectory,
        [Parameter(Mandatory = $true)]
        [string]$ScriptName
    )

    $escapedDir = $WorkingDirectory.Replace("\", "\\")
    $escapedScript = $ScriptName.Replace("\", "\\")
    $processes = Get-CimInstance Win32_Process | Where-Object {
        $_.CommandLine -like "*$escapedDir*" -and $_.CommandLine -like "*$escapedScript*"
    }

    return @($processes).Count -gt 0
}

$pythonCommand = Get-PythonCommand

if (Test-BotAlreadyRunning -WorkingDirectory $oportunidadDir -ScriptName "iniciar_todo.py") {
    if ($DryRun) {
        Write-Host "El bot unificado ya aparece como arrancado."
    } else {
        Write-Host "El bot unificado ya esta arrancado. No se abrira otra ventana."
    }
    return
}

Start-BotWindow `
    -BotName "Bot unificado" `
    -WorkingDirectory $oportunidadDir `
    -PythonCommand $pythonCommand `
    -ScriptName "iniciar_todo.py"

if ($DryRun) {
    Write-Host "Dry run completado. No se han arrancado procesos."
} else {
    Write-Host "Se ha lanzado la ventana del bot unificado."
}
