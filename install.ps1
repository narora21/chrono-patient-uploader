$ErrorActionPreference = "Stop"

$Repo = "narora21/chrono-patient-uploader"
$InstallDir = "$env:LOCALAPPDATA\chrono-uploader"
$Archive = "chrono-uploader-win.zip"
$Url = "https://github.com/$Repo/releases/latest/download/$Archive"

Write-Host "Downloading chrono-uploader for Windows..."
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
& $Exe --help | Out-Null
if ($LASTEXITCODE -eq 0) {
    Write-Host ""
    Write-Host "chrono-uploader installed successfully!"
    Write-Host ""
    Write-Host "Location: $Exe"
    Write-Host ""
    Write-Host "To run it:"
    Write-Host "  & '$Exe' <directory>"
} else {
    Write-Error "Installation verification failed."
    exit 1
}
