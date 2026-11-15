$ErrorActionPreference = 'Stop'
$lofinTaskName = 'G2B-Lofin365-Budget-2026'
$lofinRoot = 'D:\G2B\lofin-budget-v1'
$lofinPython = 'C:\Users\User\AppData\Local\Programs\Python\Python313\python.exe'
$lofinPythonw = 'C:\Users\User\AppData\Local\Programs\Python\Python313\pythonw.exe'
if (Get-ScheduledTask -TaskName $lofinTaskName -ErrorAction SilentlyContinue) { throw 'TASK_ALREADY_EXISTS' }
$lofinVerifyCode = @'
import sys,json,sqlite3
from pathlib import Path
sys.path.insert(0,'D:/G2B/lofin-budget-v1')
import lofin_scheduled_entry as entry
root=Path('D:/G2B/lofin-collection')
entry.verify_package(entry.DEPLOY)
entry.require_live_proof(root)
status=json.loads((root/'lofin-status.json').read_text(encoding='utf-8'))
if status.get('state')!='VERIFIED': raise SystemExit('LATEST_RUN_MUST_BE_VERIFIED')
c=sqlite3.connect((root/'lofin-calls.sqlite3').as_uri()+'?mode=ro',uri=True)
pending=c.execute("SELECT COUNT(*) FROM calls WHERE state NOT IN ('VERIFIED','NOT_SENT')").fetchone()[0]
c.close()
if pending: raise SystemExit('UNRESOLVED_CALL_NO_SCHEDULE')
print('LOFIN_FIRST_RESPONSE_PROOF_VERIFIED')
'@
& $lofinPython -B -c $lofinVerifyCode
if ($LASTEXITCODE -ne 0) { throw 'LIVE_PROOF_NOT_READY_NO_REGISTRATION' }
$lofinIdentity = [Security.Principal.WindowsIdentity]::GetCurrent()
$lofinStart = (Get-Date).AddMinutes(2)
$lofinAction = New-ScheduledTaskAction -Execute $lofinPythonw -Argument ('-B "' + (Join-Path $lofinRoot 'lofin_scheduled_entry.py') + '"') -WorkingDirectory $lofinRoot
$lofinTime = New-ScheduledTaskTrigger -Once -At $lofinStart -RepetitionInterval (New-TimeSpan -Minutes 15)
$lofinLogon = New-ScheduledTaskTrigger -AtLogOn -User $lofinIdentity.Name
$lofinLogon.Delay='PT45S'
$lofinPrincipal = New-ScheduledTaskPrincipal -UserId $lofinIdentity.User.Value -LogonType Interactive -RunLevel Limited
$lofinSettings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Seconds 90) -MultipleInstances IgnoreNew -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -RunOnlyIfNetworkAvailable -RestartCount 0 -DisallowDemandStart
$lofinDefinition = New-ScheduledTask -Action $lofinAction -Trigger @($lofinTime,$lofinLogon) -Principal $lofinPrincipal -Settings $lofinSettings -Description 'Independent LOFIN 2026 budget pipeline within G2B; separate key/database/holds; 8 pages/90 seconds, 100 calls per KST day; no uncertain retries. User signs in after reboot.'
Register-ScheduledTask -TaskName $lofinTaskName -InputObject $lofinDefinition | Out-Null
Export-ScheduledTask -TaskName $lofinTaskName | Set-Content -LiteralPath (Join-Path $PSScriptRoot 'evidence\lofin-automatic-task.xml') -Encoding UTF8
$lofinEvidence = [ordered]@{TaskName=$lofinTaskName;FirstTriggerKst=$lofinStart.ToString('o');Interval='PT15M';LogonDelay='PT45S';LogonType='Interactive';ExecutionTimeLimit='PT1M30S';ManualStartIssued=$false;G2BTaskChanged=$false}
$lofinEvidence | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $PSScriptRoot 'evidence\lofin-task-registration.json') -Encoding UTF8
$lofinEvidence | ConvertTo-Json
