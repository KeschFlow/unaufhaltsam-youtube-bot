$ErrorActionPreference = "Stop"

$taskName = "UNAUFHALTSAM YouTube Autopilot"
$projectRoot = "C:\Users\kesch\unaufhaltsam-youtube-bot"
$pythonExe = Join-Path $projectRoot ".venv\Scripts\python.exe"
$runner = Join-Path $projectRoot "scheduled_entry.py"

if (-not (Test-Path -LiteralPath $runner -PathType Leaf)) {
    throw "Scheduler-Runner fehlt: $runner"
}

$action = New-ScheduledTaskAction `
    -Execute $pythonExe `
    -Argument "`"$runner`"" `
    -WorkingDirectory $projectRoot
$dailyTrigger = New-ScheduledTaskTrigger -Daily -At "12:00"
$userAccount = "$env:COMPUTERNAME\kesch"
$startupTrigger = New-ScheduledTaskTrigger -AtLogOn -User $userAccount -RandomDelay (New-TimeSpan -Minutes 2)
$settings = New-ScheduledTaskSettingsSet `
    -MultipleInstances IgnoreNew `
    -StartWhenAvailable `
    -WakeToRun `
    -RunOnlyIfNetworkAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1) `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries
$principal = New-ScheduledTaskPrincipal `
    -UserId $userAccount `
    -LogonType Interactive `
    -RunLevel Limited

Register-ScheduledTask `
    -TaskName $taskName `
    -Action $action `
    -Trigger @($dailyTrigger, $startupTrigger) `
    -Settings $settings `
    -Principal $principal `
    -Description "Taeglicher begrenzter UNAUFHALTSAM YouTube Autopilot im sicheren Dry-Run-Modus." `
    -Force
