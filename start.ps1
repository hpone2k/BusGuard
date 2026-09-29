param([ValidateSet('hybrid','yoloe','grounding-dino','locateanything','demo')][string]$Backend = 'hybrid', [int]$Port = 4479, [switch]$Lan, [switch]$PassengerHttps)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$projectPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $projectPython)) {
    $projectPython = Join-Path $PSScriptRoot '..\vision4477\.venv\Scripts\python.exe'
}
if (-not (Test-Path -LiteralPath $projectPython)) {
    throw 'No project environment found. Follow the setup instructions in README.md first.'
}
$launchArguments = @('run.py', '--backend', $Backend, '--port', $Port)
if ($Lan) { $launchArguments += '--lan' }
if ($PassengerHttps) {
    $passengerCertificate = Join-Path $PSScriptRoot 'data\tls\passenger-cert.pem'
    $passengerPrivateKey = Join-Path $PSScriptRoot 'data\tls\passenger-key.pem'
    if (-not (Test-Path -LiteralPath $passengerCertificate) -or -not (Test-Path -LiteralPath $passengerPrivateKey)) {
        throw 'Create the local passenger certificate first. See docs/VOICE_SETUP.md.'
    }
    $launchArguments += @('--passenger-tls-cert', $passengerCertificate, '--passenger-tls-key', $passengerPrivateKey)
}
& $projectPython @launchArguments
exit $LASTEXITCODE
