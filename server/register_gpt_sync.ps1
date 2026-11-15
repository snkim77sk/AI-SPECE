param(
    [Parameter(Mandatory=$true)][string]$Pythonw,
    [Parameter(Mandatory=$true)][string]$RuntimeDirectory
)
$ErrorActionPreference = 'Stop'
$runtime = (Resolve-Path -LiteralPath $RuntimeDirectory).Path
$pythonPath = (Resolve-Path -LiteralPath $Pythonw).Path
$entry = Join-Path $runtime 'gpt_sync_entry.py'
if (!(Test-Path -LiteralPath $entry -PathType Leaf)) { throw 'Missing verified sync entry' }
$config = Get-Content -LiteralPath 'D:\G2B\gpt-sync-config.json' -Raw | ConvertFrom-Json
if ($config.enabled -ne $true) { throw 'Sync configuration is not enabled' }
if (!(Test-Path -LiteralPath $config.token_file -PathType Leaf)) { throw 'Missing protected sync token' }
$taskName = 'G2B-GPT-Site-Sync'
if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) { throw 'Existing task must be reviewed before replacement' }
$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$action = New-ScheduledTaskAction -Execute $pythonPath -Argument ('-B "' + $entry + '"') -WorkingDirectory $runtime
$periodic = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(2) -RepetitionInterval (New-TimeSpan -Minutes 2)
$logon = New-ScheduledTaskTrigger -AtLogOn -User $identity
$logon.Delay = 'PT60S'
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Seconds 90) -StartWhenAvailable -RunOnlyIfNetworkAvailable
$principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger @($periodic,$logon) -Settings $settings -Principal $principal -Description 'Transfer verified local pages to the existing private GPT Site; no source API collection.' | Out-Null
Get-ScheduledTask -TaskName $taskName | Select-Object TaskName,State
