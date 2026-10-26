# Put a "BOGOS bot" shortcut to the widget on the desktop and in the Start menu,
# with the BOGOS mark as its icon.
#
#   .\tools\install_shortcut.ps1
#
# Run it again after moving the repository: the shortcut points at this
# checkout's own widget, and it overwrites the one it made before.

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$widget = Join-Path $root 'widget\BOGOS-widget.ps1'
$icon = Join-Path $root 'widget\bogos.ico'
if (-not (Test-Path $widget)) { throw "No widget at $widget" }
if (-not (Test-Path $icon)) { throw "No icon at $icon" }

$powershell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$shell = New-Object -ComObject WScript.Shell
$places = @(
    [Environment]::GetFolderPath('Desktop'),
    (Join-Path ([Environment]::GetFolderPath('Programs')) '')
)
foreach ($place in $places) {
    $path = Join-Path $place 'BOGOS bot.lnk'
    $link = $shell.CreateShortcut($path)
    $link.TargetPath = $powershell
    # Hidden, and the shortcut itself starts minimised: the console PowerShell
    # needs to read the script then never shows as more than a blink.
    $link.Arguments = '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + $widget + '"'
    $link.WorkingDirectory = $root
    $link.IconLocation = $icon + ',0'
    $link.WindowStyle = 7
    $link.Description = 'BOGOS bot: start, stop and watch the transcription bot'
    $link.Save()
    Write-Output "wrote $path"
}
