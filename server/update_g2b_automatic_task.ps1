$ErrorActionPreference = 'Stop'
$targets = @(
    @{Name='G2B-Bounded-Backfill-20260101'; Root='D:\G2B\automatic-backfill-v5.2.16'; Entry='scheduled_entry.py'; Manifest='runtime-manifest.json'; Version='5.2.16'; Delay=60},
    @{Name='G2B-GPT-Site-Sync'; Root='D:\G2B\gpt-site-sync-v5.2.15'; Entry='gpt_sync_entry.py'; Manifest='runtime-sha256.json'; Version='5.2.15'; Delay=150}
)
$evidence = 'D:\G2B\maintenance\automatic-5.2.16'
New-Item -ItemType Directory -Path $evidence -Force | Out-Null
$prepared = @()
foreach ($item in $targets) {
    $task = Get-ScheduledTask -TaskName $item.Name
    if ([string]$task.State -ne 'Ready' -or $task.Actions.Count -ne 1) { throw 'Existing task must be idle with one action' }
    $manifest = Get-Content -LiteralPath (Join-Path $item.Root $item.Manifest) -Raw | ConvertFrom-Json
    if ($manifest.version -ne $item.Version) { throw 'Runtime version mismatch' }
    if ($item.Entry -eq 'scheduled_entry.py' -and $manifest.task_name -ne $item.Name) { throw 'Runtime task binding mismatch' }
    foreach ($file in $manifest.files.PSObject.Properties) {
        $path = [IO.Path]::GetFullPath((Join-Path $item.Root $file.Name))
        if (-not $path.StartsWith($item.Root + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Runtime path outside package' }
        if ((Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() -ne $file.Value) { throw 'Runtime hash mismatch' }
    }
    $periodic = @($task.Triggers | Where-Object {$_.CimClass.CimClassName -eq 'MSFT_TaskTimeTrigger'})
    $logon = @($task.Triggers | Where-Object {$_.CimClass.CimClassName -eq 'MSFT_TaskLogonTrigger'})
    if ($periodic.Count -ne 1 -or $logon.Count -ne 1 -or $task.Triggers.Count -ne 2) { throw 'Expected existing periodic and login triggers' }
    $xml = Export-ScheduledTask -TaskName $item.Name
    $backup = Join-Path $evidence ($item.Name + '-' + [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfffffff') + '.xml')
    [IO.File]::WriteAllText($backup, $xml, [Text.UTF8Encoding]::new($false))
    $prepared += @{Item=$item; Task=$task; XML=$xml; Periodic=$periodic[0]}
}
$start = Get-Date
foreach ($entry in $prepared) {
    $item=$entry.Item
    if ([string](Get-ScheduledTask -TaskName $item.Name).State -ne 'Ready') { throw 'Task began running; wait before updating' }
    $entry.Periodic.Repetition.Interval='PT2M'
    $entry.Periodic.StartBoundary=$start.AddSeconds($item.Delay).ToString('yyyy-MM-ddTHH:mm:sszzz')
    $action=New-ScheduledTaskAction -Execute $entry.Task.Actions[0].Execute -Argument ('-B "' + (Join-Path $item.Root $item.Entry) + '"') -WorkingDirectory $item.Root
    Set-ScheduledTask -TaskName $item.Name -Action $action -Trigger $entry.Task.Triggers | Out-Null
    [xml]$before=$entry.XML
    [xml]$after=Export-ScheduledTask -TaskName $item.Name
    foreach ($part in @('Settings','Principals')) {
        if ($before.Task.$part.OuterXml -ne $after.Task.$part.OuterXml) { throw ('Task policy changed: ' + $part) }
    }
    if ($before.Task.Triggers.LogonTrigger.OuterXml -ne $after.Task.Triggers.LogonTrigger.OuterXml) { throw 'Login trigger changed' }
    if ($after.Task.Triggers.TimeTrigger.Repetition.Interval -ne 'PT2M') { throw 'Periodic interval not applied' }
    if ($after.Task.Triggers.TimeTrigger.StartBoundary -ne $entry.Periodic.StartBoundary) { throw 'Next start not applied' }
    $info=Get-ScheduledTaskInfo -TaskName $item.Name
    [pscustomobject]@{Task=$item.Name;Runtime=$item.Root;Version=$item.Version;Interval='PT2M';NextRun=$info.NextRunTime.ToString('o');LoginAndLimitsPreserved=$true}
}
