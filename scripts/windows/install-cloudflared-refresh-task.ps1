$ErrorActionPreference = "Stop"

$taskName = "RPG Kingdom Supervisor - Refresh Cloudflare"
$service = Get-Service -Name cloudflared -ErrorAction Stop
if (-not $service) { throw "cloudflared Windows service was not found." }

$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument '-NoProfile -NonInteractive -WindowStyle Hidden -Command "Restart-Service cloudflared"'
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName $taskName -Action $action -Principal $principal -Settings $settings -Force | Out-Null
Write-Host "Installed on-demand elevated task: $taskName"
Write-Host "The Supervisor dashboard can now refresh Cloudflared without receiving arbitrary Windows administrator access."
