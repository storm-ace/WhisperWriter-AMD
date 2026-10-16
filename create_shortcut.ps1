# Creates Desktop + Start Menu shortcuts that launch WhisperWriter without a console window.
# Run once after installation:  powershell -ExecutionPolicy Bypass -File create_shortcut.ps1
# Add -Autostart to also start WhisperWriter at login (in the tray if "Start in tray at login" is on).
param([switch]$Autostart)
$ErrorActionPreference = 'Stop'
$repo   = $PSScriptRoot
$target = Join-Path $repo 'start_hidden.vbs'
$icon   = Join-Path $repo 'assets\whisperwriter.ico'

if (-not (Test-Path $target)) { throw "start_hidden.vbs not found next to this script ($target)." }

$sh = New-Object -ComObject WScript.Shell
function New-WWShortcut($path, $extraArgs = '') {
    $lnk = $sh.CreateShortcut($path)
    $lnk.TargetPath       = 'wscript.exe'            # runs the .vbs windowless
    $lnk.Arguments        = ('"' + $target + '" ' + $extraArgs).Trim()
    $lnk.WorkingDirectory = $repo
    if (Test-Path $icon) { $lnk.IconLocation = $icon }
    $lnk.Description       = 'Start WhisperWriter (speech-to-text)'
    $lnk.Save()
    Write-Host "Created: $path"
}

New-WWShortcut (Join-Path ([Environment]::GetFolderPath('Desktop'))  'WhisperWriter.lnk')
New-WWShortcut (Join-Path ([Environment]::GetFolderPath('Programs')) 'WhisperWriter.lnk')

if ($Autostart) {
    New-WWShortcut (Join-Path ([Environment]::GetFolderPath('Startup')) 'WhisperWriter.lnk') '--autostart'
}

Write-Host ''
if (-not $Autostart) { Write-Host 'Done. Run again with -Autostart to also start WhisperWriter at login.' }
