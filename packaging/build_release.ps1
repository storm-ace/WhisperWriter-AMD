# Builds the portable Windows release: dist\WhisperWriter-AMD-<version>-win64.zip
#
#   powershell -ExecutionPolicy Bypass -File packaging\build_release.ps1 `
#       -WhisperServer C:\tools\whisper.cpp\build\bin\whisper-server.exe
#
# Needs the .venv from the README (plus `pip install pyinstaller`) and a whisper.cpp build
# with Vulkan. For a MinGW build the runtime DLLs are taken from the folder of g++.exe
# (or -MinGWBin). The ggml model is not bundled (1.6 GB); the app downloads it on first start.
param(
    [string]$Version = '2.0.1-amd',
    [Parameter(Mandatory = $true)][string]$WhisperServer,
    [string]$MinGWBin = ''
)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot
$python = Join-Path $root '.venv\Scripts\python.exe'
$dist = Join-Path $root 'dist'
$app = Join-Path $dist 'WhisperWriter'

if (-not (Test-Path $python)) { throw ".venv not found: create it as described in the README." }
if (-not (Test-Path $WhisperServer)) { throw "whisper-server.exe not found: $WhisperServer" }

Write-Host '== PyInstaller'
& $python -m PyInstaller --noconfirm --clean --distpath $dist --workpath (Join-Path $root 'build\pyinstaller') `
    (Join-Path $PSScriptRoot 'whisperwriter.spec')
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed' }

Write-Host '== Program folder'
Move-Item -Force (Join-Path $dist 'whisperwriter-mcp.exe') $app
New-Item -ItemType Directory -Force (Join-Path $app 'assets') | Out-Null
foreach ($f in 'whisperwriter.ico', 'whisperwriter.png', 'beep.wav') {
    Copy-Item (Join-Path $root "assets\$f") (Join-Path $app 'assets')
}
Copy-Item (Join-Path $root 'LICENSE') $app
Copy-Item (Join-Path $root 'README.md') $app

Write-Host '== whisper.cpp server'
$server = Join-Path $app 'whisper.cpp'
New-Item -ItemType Directory -Force $server | Out-Null
Copy-Item $WhisperServer $server
Get-ChildItem (Split-Path $WhisperServer) -Filter *.dll -ErrorAction SilentlyContinue | Copy-Item -Destination $server
if (-not $MinGWBin) {
    $gpp = Get-Command g++.exe -ErrorAction SilentlyContinue
    if ($gpp) { $MinGWBin = Split-Path $gpp.Source }
}
foreach ($dll in 'libgcc_s_seh-1.dll', 'libstdc++-6.dll', 'libwinpthread-1.dll', 'libgomp-1.dll', 'libdl.dll') {
    $path = if ($MinGWBin) { Join-Path $MinGWBin $dll } else { '' }
    if ($path -and (Test-Path $path)) { Copy-Item $path $server }
    else { Write-Warning "$dll not found (only needed for MinGW builds of whisper-server)" }
}

Write-Host '== models folder'
$models = Join-Path $app 'models'
New-Item -ItemType Directory -Force $models | Out-Null
@'
WhisperWriter downloads ggml-large-v3-turbo.bin into this folder on the first start.
To use another whisper.cpp ggml model, put it here; it is picked up automatically.

Manual download of the default (1.6 GB):
  curl -L -o ggml-large-v3-turbo.bin https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo.bin

Others: ggml-large-v3.bin (most accurate), ggml-small.bin (466 MB, for weak GPUs).
'@ | Set-Content -Encoding UTF8 (Join-Path $models 'README.txt')

Write-Host '== Zip'
$zip = Join-Path $dist "WhisperWriter-AMD-$Version-win64.zip"
if (Test-Path $zip) { Remove-Item $zip }
Compress-Archive -Path $app -DestinationPath $zip -CompressionLevel Optimal
$size = [math]::Round((Get-Item $zip).Length / 1MB, 1)
Write-Host "Done: $zip ($size MB)"
