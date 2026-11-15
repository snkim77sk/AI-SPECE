$ErrorActionPreference = 'Stop'
$g2bTaskName = 'G2B-Bounded-Backfill-20260101'
$g2bDeployRoot = 'D:\G2B\automatic-backfill-20261114'
$g2bPython = 'C:\Users\User\AppData\Local\Programs\Python\Python313\pythonw.exe'
$g2bEntry = Join-Path $g2bDeployRoot 'scheduled_entry.py'
if (Get-ScheduledTask -TaskName $g2bTaskName -ErrorAction SilentlyContinue) {
    throw 'TASK_ALREADY_EXISTS_NO_OVERWRITE'
}
if (!(Test-Path -LiteralPath $g2bPython) -or !(Test-Path -LiteralPath $g2bEntry)) {
    throw 'VERIFIED_RUNTIME_REQUIRED'
}
$g2bIdentity = [Security.Principal.WindowsIdentity]::GetCurrent()
$g2bStart = (Get-Date).AddMinutes(2)
$g2bAction = New-ScheduledTaskAction -Execute $g2bPython -Argument ('-B "' + $g2bEntry + '"') -WorkingDirectory $g2bDeployRoot
$g2bTrigger = New-ScheduledTaskTrigger -Once -At $g2bStart -RepetitionInterval (New-TimeSpan -Minutes 15)
$g2bPrincipal = New-ScheduledTaskPrincipal -UserId $g2bIdentity.User.Value -LogonType Interactive -RunLevel Limited
$g2bSettings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Seconds 90) -MultipleInstances IgnoreNew -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -RunOnlyIfNetworkAvailable -RestartCount 0 -DisallowDemandStart
$g2bDefinition = New-ScheduledTask -Action $g2bAction -Trigger $g2bTrigger -Principal $g2bPrincipal -Settings $g2bSettings -Description 'G2B newest-first through 2026-01-01; 8 pages/90 sec, KST daily budget <=950; uncertain requests remain held. Existing signed-in user session required.'
# No -Force, passwords, service credentials, or existing-task edits.
Register-ScheduledTask -TaskName $g2bTaskName -InputObject $g2bDefinition | Out-Null
$g2bRegistered = Get-ScheduledTask -TaskName $g2bTaskName
$g2bInfo = Get-ScheduledTaskInfo -TaskName $g2bTaskName
Export-ScheduledTask -TaskName $g2bTaskName | Set-Content -LiteralPath (Join-Path $PSScriptRoot 'evidence\automatic-task.xml') -Encoding UTF8
$g2bEvidence = [ordered]@{
    TaskName = $g2bTaskName
    State = [string]$g2bRegistered.State
    FirstTriggerLocal = $g2bStart.ToString('o')
    NextRunTime = $g2bInfo.NextRunTime.ToString('o')
    Interval = [string]$g2bRegistered.Triggers[0].Repetition.Interval
    RepetitionDuration = [string]$g2bRegistered.Triggers[0].Repetition.Duration
    ExecutionTimeLimit = [string]$g2bRegistered.Settings.ExecutionTimeLimit
    MultipleInstances = [string]$g2bRegistered.Settings.MultipleInstances
    RestartCount = $g2bRegistered.Settings.RestartCount
    LogonType = [string]$g2bRegistered.Principal.LogonType
    RunLevel = [string]$g2bRegistered.Principal.RunLevel
    Execute = $g2bRegistered.Actions[0].Execute
    Arguments = $g2bRegistered.Actions[0].Arguments
    WorkingDirectory = $g2bRegistered.Actions[0].WorkingDirectory
    ManualStartIssued = $false
}
$g2bEvidence | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $PSScriptRoot 'evidence\automatic-task-registration.json') -Encoding UTF8
$g2bEvidence | ConvertTo-Json -Depth 4
