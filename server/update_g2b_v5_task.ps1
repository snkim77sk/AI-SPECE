param([switch]$Apply)
$ErrorActionPreference='Stop'
$taskName='G2B-Bounded-Backfill-20260101'
$newRoot='D:\G2B\automatic-backfill-v5'
$task=Get-ScheduledTask -TaskName $taskName
if ($task.State -ne 'Ready' -or $task.Principal.LogonType -ne 'Interactive') { throw 'IDLE_INTERACTIVE_TASK_REQUIRED' }
if ($task.Actions.Count -ne 1 -or $task.Actions[0].WorkingDirectory -ne 'D:\G2B\automatic-backfill-v4') { throw 'EXPECTED_V4_ACTION_REQUIRED' }
$manifest=Get-Content -LiteralPath "$newRoot\runtime-manifest.json" -Raw -Encoding UTF8 | ConvertFrom-Json
foreach ($file in $manifest.files.PSObject.Properties) {
    $target=[IO.Path]::GetFullPath((Join-Path $newRoot $file.Name))
    if (!$target.StartsWith($newRoot+'\',[StringComparison]::OrdinalIgnoreCase)) { throw 'INVALID_PACKAGE_PATH' }
    if ((Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash.ToLowerInvariant() -ne $file.Value) { throw 'PACKAGE_CHANGED' }
}
if ((Get-FileHash -LiteralPath 'D:\G2B\isolated-prep-20261113\AUTO_COLLECTION_HOLD.json' -Algorithm SHA256).Hash.ToLowerInvariant() -ne $manifest.historical_hold_sha256) { throw 'HISTORICAL_HOLD_CHANGED' }
$proof=Get-Content -LiteralPath 'D:\G2B\live-bounded-20261114\manual-schema-recheck-20261115-01\result.json' -Raw -Encoding UTF8 | ConvertFrom-Json
if ($proof.state -ne 'VERIFIED' -or !$proof.readback.verified -or !$proof.historical_hold_unchanged) { throw 'MANUAL_RESPONSE_AND_READBACK_REQUIRED' }
$review=[ordered]@{TaskName=$taskName;NewPackage=$newRoot;Apply=[bool]$Apply;ScheduleChanged=$false;CredentialsChanged=$false;ManualStartIssued=$false;Applied=$false}
if ($Apply) {
    Export-ScheduledTask -TaskName $taskName | Set-Content -LiteralPath "$PSScriptRoot\evidence\g2b-task-before-v5.xml" -Encoding UTF8
    $task.Actions=@(New-ScheduledTaskAction -Execute $task.Actions[0].Execute -Argument ('-B "'+$newRoot+'\scheduled_entry.py"') -WorkingDirectory $newRoot)
    $task.Description='G2B daily update and newest-first Jan 1 backfill; isolate failed dates, keep other dates running; 8 pages/90sec/950 per KST day; current-user logon plus 15-minute timer.'
    Set-ScheduledTask -InputObject $task | Out-Null
    Export-ScheduledTask -TaskName $taskName | Set-Content -LiteralPath "$PSScriptRoot\evidence\g2b-task-after-v5.xml" -Encoding UTF8
    $review.Applied=$true
    $review | ConvertTo-Json | Set-Content -LiteralPath "$PSScriptRoot\evidence\g2b-task-v5-update.json" -Encoding UTF8
}
$review | ConvertTo-Json
