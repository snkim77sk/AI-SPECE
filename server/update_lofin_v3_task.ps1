param([switch]$Apply)
$ErrorActionPreference='Stop'
$taskName='G2B-Lofin365-Budget-2026'
$newRoot='D:\G2B\lofin-budget-v3'
$task=Get-ScheduledTask -TaskName $taskName
if ($task.State -ne 'Ready' -or $task.Principal.LogonType -ne 'Interactive') { throw 'IDLE_INTERACTIVE_TASK_REQUIRED' }
if ($task.Actions.Count -ne 1 -or $task.Actions[0].WorkingDirectory -ne 'D:\G2B\lofin-budget-v2') { throw 'EXPECTED_V2_ACTION_REQUIRED' }
python -B -c "import sys;sys.path.insert(0,'D:/G2B/lofin-budget-v3');import lofin_scheduled_entry as s;from pathlib import Path;s.verify_package(s.ROOT);s.require_live_proof(Path('D:/G2B/lofin-collection'))"
if ($LASTEXITCODE -ne 0) { throw 'COLLECTOR_PACKAGE_AND_PRIOR_RESPONSE_PROOF_REQUIRED' }
$review=[ordered]@{TaskName=$taskName;NewPackage=$newRoot;Apply=[bool]$Apply;ScheduleChanged=$false;CredentialsChanged=$false;ManualStartIssued=$false;Applied=$false}
if ($Apply) {
    Export-ScheduledTask -TaskName $taskName | Set-Content -LiteralPath "$PSScriptRoot\evidence\lofin-task-before-v3.xml" -Encoding UTF8
    $task.Actions=@(New-ScheduledTaskAction -Execute $task.Actions[0].Execute -Argument ('-B "'+$newRoot+'\lofin_scheduled_entry.py"') -WorkingDirectory $newRoot)
    $task.Description='LOFIN budget auto collection; recover proven unsent deadline reservations; isolate uncertain snapshots and continue other scopes; 8 pages/90sec/100 per KST day; manual Windows logon plus 15-minute timer.'
    Set-ScheduledTask -InputObject $task | Out-Null
    Export-ScheduledTask -TaskName $taskName | Set-Content -LiteralPath "$PSScriptRoot\evidence\lofin-task-after-v3.xml" -Encoding UTF8
    $review.Applied=$true
    $review | ConvertTo-Json | Set-Content -LiteralPath "$PSScriptRoot\evidence\lofin-task-v3-update.json" -Encoding UTF8
}
$review | ConvertTo-Json
