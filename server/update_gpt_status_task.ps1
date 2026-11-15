param(
    [Parameter(Mandatory=$true)][string]$RuntimeDirectory
)
$ErrorActionPreference = 'Stop'
$runtime = (Resolve-Path -LiteralPath $RuntimeDirectory).Path
$entry = Join-Path $runtime 'gpt_sync_entry.py'
if (!(Test-Path -LiteralPath $entry -PathType Leaf) -or !(Test-Path -LiteralPath (Join-Path $runtime 'collector_status.py') -PathType Leaf)) { throw 'Verified status runtime is required' }
$name = 'G2B-GPT-Site-Sync'
$task = Get-ScheduledTask -TaskName $name
if ($task.State.ToString() -eq 'Running') { throw 'Wait for the existing sync run to finish' }
if ($task.Actions.Count -ne 1) { throw 'Unexpected task actions' }
$python = $task.Actions[0].Execute
if (!(Test-Path -LiteralPath $python -PathType Leaf)) { throw 'Existing Python executable is unavailable' }
[xml]$before = Export-ScheduledTask -TaskName $name
$action = New-ScheduledTaskAction -Execute $python -Argument ('-B "' + $entry + '"') -WorkingDirectory $runtime
Set-ScheduledTask -TaskName $name -Action $action | Out-Null
[xml]$after = Export-ScheduledTask -TaskName $name
foreach ($part in @('Triggers','Settings','Principals')) {
    if ($before.Task.$part.OuterXml -ne $after.Task.$part.OuterXml) { throw ('Unexpected task change: ' + $part) }
}
$updated = Get-ScheduledTask -TaskName $name
[PSCustomObject]@{
    TaskName=$updated.TaskName
    State=$updated.State.ToString()
    Runtime=$updated.Actions[0].WorkingDirectory
    ScheduleAndLimitsUnchanged=$true
} | ConvertTo-Json
