# Run deliberately on the host with PowerShell 7. This creates files only;
# it never installs a trusted root, changes the firewall, or starts a service.
param([Parameter(Mandatory = $true)][string]$IPAddress)
$ErrorActionPreference = 'Stop'
if ($PSVersionTable.PSVersion.Major -lt 7) {
    throw 'Run this script with PowerShell 7 (pwsh), which supports PEM key export.'
}
$address = [System.Net.IPAddress]::Parse($IPAddress)
if ($address.AddressFamily -ne [System.Net.Sockets.AddressFamily]::InterNetwork) {
    throw 'Use the host computer IPv4 address shown by the BusGuard LAN console.'
}
$tlsDirectory = Join-Path (Split-Path -Parent $PSScriptRoot) 'data\tls'
[System.IO.Directory]::CreateDirectory($tlsDirectory) | Out-Null
$privatePath = Join-Path $tlsDirectory 'passenger-key.pem'
$certificatePath = Join-Path $tlsDirectory 'passenger-cert.pem'
$publicRootPath = Join-Path $tlsDirectory 'BusGuard-demo-root.cer'
foreach ($file in @($privatePath, $certificatePath, $publicRootPath)) {
    if (Test-Path -LiteralPath $file) { throw "TLS files already exist at $tlsDirectory. Keep them, or move them aside before deliberately issuing new certificates." }
}
$rootKey = [System.Security.Cryptography.RSA]::Create(2048)
$serverKey = [System.Security.Cryptography.RSA]::Create(2048)
$rootCertificate = $null
$serverCertificate = $null
try {
    $hash = [System.Security.Cryptography.HashAlgorithmName]::SHA256
    $padding = [System.Security.Cryptography.RSASignaturePadding]::Pkcs1
    $rootRequest = [System.Security.Cryptography.X509Certificates.CertificateRequest]::new('CN=BusGuard temporary demo CA', $rootKey, $hash, $padding)
    $rootRequest.CertificateExtensions.Add([System.Security.Cryptography.X509Certificates.X509BasicConstraintsExtension]::new($true, $false, 0, $true))
    $rootRequest.CertificateExtensions.Add([System.Security.Cryptography.X509Certificates.X509KeyUsageExtension]::new([System.Security.Cryptography.X509Certificates.X509KeyUsageFlags]::KeyCertSign, $true))
    $rootRequest.CertificateExtensions.Add([System.Security.Cryptography.X509Certificates.X509SubjectKeyIdentifierExtension]::new($rootRequest.PublicKey, $false))
    $now = [System.DateTimeOffset]::UtcNow
    $rootCertificate = $rootRequest.CreateSelfSigned($now.AddMinutes(-5), $now.AddDays(35))
    $serverRequest = [System.Security.Cryptography.X509Certificates.CertificateRequest]::new('CN=BusGuard passenger demo', $serverKey, $hash, $padding)
    $serverRequest.CertificateExtensions.Add([System.Security.Cryptography.X509Certificates.X509BasicConstraintsExtension]::new($false, $false, 0, $true))
    $serverRequest.CertificateExtensions.Add([System.Security.Cryptography.X509Certificates.X509KeyUsageExtension]::new([System.Security.Cryptography.X509Certificates.X509KeyUsageFlags]::DigitalSignature, $true))
    $purposes = [System.Security.Cryptography.OidCollection]::new()
    $purposes.Add([System.Security.Cryptography.Oid]::new('1.3.6.1.5.5.7.3.1')) | Out-Null
    $serverRequest.CertificateExtensions.Add([System.Security.Cryptography.X509Certificates.X509EnhancedKeyUsageExtension]::new($purposes, $true))
    $names = [System.Security.Cryptography.X509Certificates.SubjectAlternativeNameBuilder]::new()
    $names.AddIpAddress($address)
    $names.AddIpAddress([System.Net.IPAddress]::Loopback)
    $names.AddDnsName('localhost')
    $serverRequest.CertificateExtensions.Add($names.Build())
    $serial = [System.Security.Cryptography.RandomNumberGenerator]::GetBytes(16)
    $serverCertificate = $serverRequest.Create($rootCertificate, $now.AddMinutes(-5), $now.AddDays(30), $serial)
    $principals = @((& (Join-Path $PSScriptRoot 'private_file_principals.ps1') -WorkspacePath (Split-Path -Parent $PSScriptRoot)) | ConvertFrom-Json)
    [System.IO.File]::WriteAllText($privatePath, '')
    $permissionArguments = @($privatePath, '/inheritance:r', '/grant:r')
    foreach ($principal in $principals) { $permissionArguments += "*${principal}:(F)" }
    & icacls @permissionArguments | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Private key permissions could not be applied; no key was written.' }
    [System.IO.File]::WriteAllText($privatePath, $serverKey.ExportPkcs8PrivateKeyPem())
    [System.IO.File]::WriteAllText($certificatePath, $serverCertificate.ExportCertificatePem() + "`n" + $rootCertificate.ExportCertificatePem())
    [System.IO.File]::WriteAllBytes($publicRootPath, $rootCertificate.RawData)
    $fingerprint = [Convert]::ToHexString([System.Security.Cryptography.SHA256]::HashData($rootCertificate.RawData))
    Write-Host "Created a 30-day local HTTPS certificate for $IPAddress."
    Write-Host "Public CA certificate for your demo phone: $publicRootPath"
    Write-Host "Compare this SHA-256 fingerprint before choosing to trust it: $fingerprint"
    Write-Host 'No certificate was installed or trusted by this script. The private key must stay on the host.'
    Write-Host 'See docs/VOICE_SETUP.md for Android/iPhone trust and server launch steps.'
} finally {
    if ($serverCertificate) { $serverCertificate.Dispose() }
    if ($rootCertificate) { $rootCertificate.Dispose() }
    $serverKey.Dispose()
    $rootKey.Dispose()
}
