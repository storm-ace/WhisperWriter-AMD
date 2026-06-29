# Creates Desktop + Start Menu shortcuts that launch WhisperWriter hidden (no console window).
# Run once after installation:  powershell -ExecutionPolicy Bypass -File create_shortcut.ps1
$ErrorActionPreference = 'Stop'
$repo   = $PSScriptRoot
$target = Join-Path $repo 'start_hidden.vbs'
$icon   = Join-Path $repo 'assets\ww-logo.ico'

if (-not (Test-Path $target)) { throw "start_hidden.vbs not found next to this script ($target)." }

$sh = New-Object -ComObject WScript.Shell
function New-WWShortcut($path) {
    $lnk = $sh.CreateShortcut($path)
    $lnk.TargetPath       = 'wscript.exe'            # runs the .vbs windowless
    $lnk.Arguments        = '"' + $target + '"'
    $lnk.WorkingDirectory = $repo
    if (Test-Path $icon) { $lnk.IconLocation = $icon }
    $lnk.Description       = 'Start WhisperWriter (speech-to-text)'
    $lnk.Save()
    Write-Host "Created: $path"
}

New-WWShortcut (Join-Path ([Environment]::GetFolderPath('Desktop'))  'WhisperWriter.lnk')
New-WWShortcut (Join-Path ([Environment]::GetFolderPath('Programs')) 'WhisperWriter.lnk')

Write-Host ''
Write-Host 'Done. For autostart at login, copy start_hidden.vbs into the folder that "shell:startup" opens.'
