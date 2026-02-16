param(
    [string]$Version = "latest"
)

$ErrorActionPreference = "Stop"

$Repo = "narora21/chrono-patient-uploader"
$InstallDir = "$env:LOCALAPPDATA\chrono-uploader"
$Archive = "chrono-uploader-win.zip"
if ($Version -eq "latest") {
    $Url = "https://github.com/$Repo/releases/latest/download/$Archive"
} else {
    $Url = "https://github.com/$Repo/releases/download/$Version/$Archive"
}

Write-Host "Downloading chrono-uploader $Version for Windows..."
$TmpDir = New-Item -ItemType Directory -Path ([System.IO.Path]::GetTempPath() + [System.Guid]::NewGuid().ToString())
$ZipPath = Join-Path $TmpDir $Archive
Invoke-WebRequest -Uri $Url -OutFile $ZipPath

Write-Host "Installing to $InstallDir..."
if (Test-Path $InstallDir) { Remove-Item -Recurse -Force $InstallDir }
Expand-Archive -Path $ZipPath -DestinationPath $InstallDir -Force
# Move files from nested folder to install dir
$Nested = Join-Path $InstallDir "chrono-uploader-win"
if (Test-Path $Nested) {
    Get-ChildItem $Nested | Move-Item -Destination $InstallDir
    Remove-Item $Nested
}
Remove-Item -Recurse -Force $TmpDir

Write-Host "Verifying installation..."
$Exe = Join-Path $InstallDir "chrono-uploader.exe"
& $Exe --version | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Error "Installation verification failed."
    exit 1
}

# Add to user PATH
$UserPath = [Environment]::GetEnvironmentVariable("Path", "User")
if ($UserPath -notlike "*$InstallDir*") {
    [Environment]::SetEnvironmentVariable("Path", "$InstallDir;$UserPath", "User")
    $env:Path = "$InstallDir;$env:Path"
    Write-Host "Added chrono-uploader to your PATH."
} else {
    Write-Host "chrono-uploader already in PATH."
}

Write-Host ""
Write-Host "chrono-uploader installed successfully!"
Write-Host ""
Write-Host "Restart your terminal, then run:"
Write-Host "  chrono-uploader <directory>"
