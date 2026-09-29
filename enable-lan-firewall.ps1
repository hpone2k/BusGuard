# Allows the three prototype websites and passenger HTTPS voice from the local subnet.
$ErrorActionPreference = 'Stop'
trap {
    $_.Exception.Message | Set-Content -LiteralPath (Join-Path $PSScriptRoot 'data\lan-firewall-error.txt')
    exit 1
}
$busIdentity = [Security.Principal.WindowsIdentity]::GetCurrent()
$busPrincipal = New-Object Security.Principal.WindowsPrincipal($busIdentity)
if (-not $busPrincipal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Start-Process -FilePath 'powershell.exe' -Verb RunAs -WindowStyle Hidden -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-File',('"' + $PSCommandPath + '"'))
    exit
}
$busPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $busPython)) { $busPython = Join-Path $PSScriptRoot '..\vision4477\.venv\Scripts\python.exe' }
if (-not (Test-Path -LiteralPath $busPython)) { throw 'Set up the Python environment first.' }
$busExecutable = (& $busPython -c 'import sys; print(sys._base_executable)').Trim()
if (-not (Test-Path -LiteralPath $busExecutable)) { throw 'Cannot locate the server executable.' }
$busInterfaces = @(Get-NetAdapter -Physical | Where-Object Status -eq 'Up' | Select-Object -ExpandProperty ifIndex)
$busAddresses = @(Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.InterfaceIndex -in $busInterfaces -and $_.IPAddress -notlike '169.254.*' -and $_.IPAddress -notlike '127.*' } | Select-Object -ExpandProperty IPAddress)
if ($busAddresses.Count -eq 0) { throw 'Connect to Wi-Fi or Ethernet before enabling LAN access.' }
$busRuleName = 'BusTech-LAN-Demo'
$busRule = Get-NetFirewallRule -Name $busRuleName -ErrorAction SilentlyContinue
if ($busRule) { $busRule | Remove-NetFirewallRule }
New-NetFirewallRule -Name $busRuleName -DisplayName 'BusTech LAN demo websites (local subnet)' -Direction Inbound -Action Allow -Protocol TCP -LocalPort 4479,4480,4481,4482 -LocalAddress $busAddresses -RemoteAddress LocalSubnet -Profile Any -Program $busExecutable | Out-Null
@{ enabled = $true; addresses = $busAddresses; program = $busExecutable; ports = @(4479,4480,4481,4482) } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $PSScriptRoot 'data\lan-firewall.json')
