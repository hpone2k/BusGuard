# Read-only identity resolution shared by the key and HTTPS setup helpers.
# A sandbox service account is not the person who owns the desktop profile.
param([Parameter(Mandatory = $true)][string]$WorkspacePath)
$ErrorActionPreference = 'Stop'
$currentIdentity = [System.Security.Principal.WindowsIdentity]::GetCurrent()
$currentSid = $currentIdentity.User.Value
if ($currentSid -notmatch '^S-1-(5-21|12-1)-(\d+-){3}\d+$') {
    throw 'Run private setup in your own Windows user account.'
}
$principals = @($currentSid)
if ($currentIdentity.Name -match '(?i)\\CodexSandbox') {
    $workspace = [System.IO.Path]::GetFullPath($WorkspacePath).TrimEnd('\')
    $profiles = @()
    foreach ($entry in Get-ChildItem -LiteralPath 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\ProfileList') {
        if ($entry.PSChildName -notmatch '^S-1-(5-21|12-1)-(\d+-){3}\d+$') { continue }
        $record = Get-ItemProperty -LiteralPath $entry.PSPath -Name ProfileImagePath
        $profile = [Environment]::ExpandEnvironmentVariables($record.ProfileImagePath).TrimEnd('\')
        if ($workspace.Equals($profile, [StringComparison]::OrdinalIgnoreCase) -or
            $workspace.StartsWith($profile + '\', [StringComparison]::OrdinalIgnoreCase)) {
            $profiles += [PSCustomObject]@{ Sid = $entry.PSChildName; Length = $profile.Length }
        }
    }
    $profiles = @($profiles | Sort-Object Length -Descending)
    if ($profiles.Count -eq 0) { throw 'Cannot identify the desktop profile. Run setup in your own Windows terminal.' }
    $owners = @($profiles | Where-Object Length -eq $profiles[0].Length | Select-Object -ExpandProperty Sid -Unique)
    if ($owners.Count -ne 1 -or $owners[0] -eq $currentSid) {
        throw 'Cannot identify a distinct desktop profile. Run setup in your own Windows terminal.'
    }
    # The named desktop user can edit; the existing local service can still load.
    # Never grant a broad group or infer ownership from sandbox-created folders.
    $principals = @($owners[0], $currentSid)
}
ConvertTo-Json -InputObject $principals -Compress
