$ErrorActionPreference = 'Stop'
$targets = @(
    @{Name='G2B-Bounded-Backfill-20260101'; Root='D:\G2B\automatic-backfill-v5.2.14'; Entry='scheduled_entry.py'}
)
$evidence = 'D:\G2B\maintenance\automatic-5.2.14'
New-Item -ItemType Directory -Path $evidence -Force | Out-Null
$prepared = @()
foreach ($item in $targets) {
    $task = Get-ScheduledTask -TaskName $item.Name
    if ([string]$task.State -ne 'Ready' -or $task.Actions.Count -ne 1) { throw 'Existing collector must be idle with one action' }
    $manifest = Get-Content -LiteralPath (Join-Path $item.Root 'runtime-manifest.json') -Raw | ConvertFrom-Json
    if ($manifest.task_name -ne $item.Name -or $manifest.version -ne '5.2.14') { throw 'Runtime binding mismatch' }
    foreach ($file in $manifest.files.PSObject.Properties) {
        $path = [IO.Path]::GetFullPath((Join-Path $item.Root $file.Name))
        if (-not $path.StartsWith($item.Root + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Runtime path outside package' }
        if ((Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() -ne $file.Value) { throw 'Runtime hash mismatch' }
    }
    $xml = Export-ScheduledTask -TaskName $item.Name
    $backup = Join-Path $evidence ($item.Name + '-' + [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfffffff') + '.xml')
    [IO.File]::WriteAllText($backup, $xml, [Text.UTF8Encoding]::new($false))
    $prepared += @{Item=$item; Task=$task; XML=$xml}
}
foreach ($entry in $prepared) {
    $item=$entry.Item
    if ([string](Get-ScheduledTask -TaskName $item.Name).State -ne 'Ready') { throw 'Collector began running; leave its action unchanged' }
    $action=New-ScheduledTaskAction -Execute $entry.Task.Actions[0].Execute -Argument ('-B "' + (Join-Path $item.Root $item.Entry) + '"') -WorkingDirectory $item.Root
    Set-ScheduledTask -TaskName $item.Name -Action $action | Out-Null
    [xml]$before=$entry.XML
    [xml]$after=Export-ScheduledTask -TaskName $item.Name
    foreach ($part in @('Triggers','Settings','Principals')) {
        if ($before.Task.$part.OuterXml -ne $after.Task.$part.OuterXml) { throw ('Task policy changed: ' + $part) }
    }
    [pscustomobject]@{Task=$item.Name;Runtime=$item.Root;Version='5.2.14';SchedulePreserved=$true}
}
