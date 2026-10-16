# BOGOS bot widget.
#
# A small always-on-top panel with one toggle. Windows 10 has no widget board,
# and the Windows 11 one needs an MSIX-packaged provider published through the
# Store, so a floating window is the closest thing that works locally.
#
# The project root is derived from this file's location, so the widget travels
# with the repository instead of hardcoding a path.

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()

# Continue, not Stop: a transient failure in the refresh timer must not kill a
# window the user is looking at.
$ErrorActionPreference = 'Continue'

$root = Split-Path -Parent $PSScriptRoot
$runner = Join-Path $root 'run.ps1'
$logFile = Join-Path $root 'bot.log'
$errFile = Join-Path $root 'bot.err.log'
$stateFile = Join-Path $PSScriptRoot 'widget-state.json'
# Shared with recording.py, which derives the same path from its own location.
$recordingFile = Join-Path $root 'recording.json'

function Get-BotProcess {
    # The bot is a python process running main.py. Nothing in this script's own
    # command line contains that string, so it cannot match itself.
    Get-CimInstance Win32_Process -Filter "Name like '%python%'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like '*main.py*' } |
        Select-Object -First 1
}

function Start-Bot {
    if (Get-BotProcess) { return }
    $starter = @{
        FilePath               = 'powershell.exe'
        ArgumentList           = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $runner)
        WorkingDirectory       = $root
        WindowStyle            = 'Hidden'
        RedirectStandardOutput = $logFile
        RedirectStandardError  = $errFile
    }
    Start-Process @starter
}

function Stop-Bot {
    # /T kills the tree. Killing only the parent is what left orphaned
    # multiprocessing children holding the project folder open for hours.
    foreach ($proc in @(Get-CimInstance Win32_Process -Filter "Name like '%python%'" -ErrorAction SilentlyContinue |
                        Where-Object { $_.CommandLine -like '*main.py*' })) {
        & taskkill.exe /PID $proc.ProcessId /T /F 2>&1 | Out-Null
    }
}

function Get-LastLogLine {
    if (-not (Test-Path $logFile)) { return '' }
    $line = Get-Content $logFile -Tail 1 -ErrorAction SilentlyContinue
    if (-not $line) { return 'log is empty' }
    # Strip the timestamp and level so the panel shows the message.
    $trimmed = ($line -split [char]0x2502)[-1].Trim()
    if (-not $trimmed) { $trimmed = $line.Trim() }
    if ($trimmed.Length -gt 46) { $trimmed = $trimmed.Substring(0, 46) + '...' }
    return $trimmed
}

function Get-Recording {
    if (-not (Test-Path $recordingFile)) { return @{ enabled = $false; folder = '' } }
    try {
        $d = Get-Content $recordingFile -Raw | ConvertFrom-Json
        return @{ enabled = [bool]$d.enabled; folder = [string]$d.folder }
    } catch {
        return @{ enabled = $false; folder = '' }
    }
}

function Set-Recording($enabled, $folder) {
    # No BOM: Python parses this with json.loads. The two key names are the
    # contract with recording.py, which pins them in its tests.
    $json = @{ enabled = [bool]$enabled; folder = [string]$folder } | ConvertTo-Json
    [System.IO.File]::WriteAllText($recordingFile, $json, (New-Object System.Text.UTF8Encoding($false)))
}

function Choose-Folder {
    $dlg = New-Object System.Windows.Forms.FolderBrowserDialog
    $dlg.Description = 'Where should the bot keep recordings?'
    $dlg.ShowNewFolderButton = $true
    $current = (Get-Recording).folder
    if ($current -and (Test-Path $current)) { $dlg.SelectedPath = $current }
    if ($dlg.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) { return $dlg.SelectedPath }
    return ''
}

function Get-Detail($proc) {
    # The widget only has a log for bots it started itself. One launched from
    # the desktop shortcut logs to its own console, so say that rather than
    # showing a stale line from an older run.
    if (-not $proc) { return 'idle' }
    if (-not (Test-Path $logFile)) { return 'started outside the widget' }
    $log = Get-Item $logFile
    if ($log.LastWriteTime -lt $proc.CreationDate) { return 'started outside the widget' }
    return Get-LastLogLine
}

function Load-Position {
    if (Test-Path $stateFile) {
        try {
            $s = Get-Content $stateFile -Raw | ConvertFrom-Json
            return New-Object System.Drawing.Point([int]$s.X, [int]$s.Y)
        } catch { }
    }
    $screen = [System.Windows.Forms.Screen]::PrimaryScreen.WorkingArea
    return New-Object System.Drawing.Point(($screen.Right - 340), 60)
}

function Save-Position {
    try {
        @{ X = $form.Location.X; Y = $form.Location.Y } |
            ConvertTo-Json | Set-Content -Path $stateFile -Encoding UTF8
    } catch { }
}

$ink = [System.Drawing.Color]::FromArgb(28, 28, 32)
$inkSoft = [System.Drawing.Color]::FromArgb(38, 38, 44)
$paper = [System.Drawing.Color]::FromArgb(238, 238, 242)
$muted = [System.Drawing.Color]::FromArgb(150, 150, 160)
$green = [System.Drawing.Color]::FromArgb(64, 190, 110)
$red = [System.Drawing.Color]::FromArgb(200, 80, 80)
$grey = [System.Drawing.Color]::FromArgb(110, 110, 120)

$form = New-Object System.Windows.Forms.Form
$form.FormBorderStyle = 'None'
$form.StartPosition = 'Manual'
$form.Location = Load-Position
$form.Size = New-Object System.Drawing.Size(320, 190)
# Not topmost: the panel should fall behind other windows like anything else.
$form.TopMost = $false
$form.ShowInTaskbar = $false
$form.BackColor = $ink

$dot = New-Object System.Windows.Forms.Panel
$dot.Location = New-Object System.Drawing.Point(18, 22)
$dot.Size = New-Object System.Drawing.Size(10, 10)
$dot.BackColor = $grey
$form.Controls.Add($dot)

$title = New-Object System.Windows.Forms.Label
$title.Text = 'BOGOS bot'
$title.Font = New-Object System.Drawing.Font('Segoe UI', 10, [System.Drawing.FontStyle]::Bold)
$title.ForeColor = $paper
$title.Location = New-Object System.Drawing.Point(36, 16)
$title.AutoSize = $true
$form.Controls.Add($title)

$minimise = New-Object System.Windows.Forms.Label
$minimise.Text = '_'
$minimise.Font = New-Object System.Drawing.Font('Segoe UI', 10, [System.Drawing.FontStyle]::Bold)
$minimise.ForeColor = $muted
$minimise.Location = New-Object System.Drawing.Point(268, 12)
$minimise.AutoSize = $true
$minimise.Cursor = [System.Windows.Forms.Cursors]::Hand
$form.Controls.Add($minimise)

$close = New-Object System.Windows.Forms.Label
$close.Text = 'x'
$close.Font = New-Object System.Drawing.Font('Segoe UI', 10)
$close.ForeColor = $muted
$close.Location = New-Object System.Drawing.Point(292, 14)
$close.AutoSize = $true
$close.Cursor = [System.Windows.Forms.Cursors]::Hand
$form.Controls.Add($close)

$status = New-Object System.Windows.Forms.Label
$status.Text = 'checking...'
$status.Font = New-Object System.Drawing.Font('Segoe UI', 9)
$status.ForeColor = $muted
$status.Location = New-Object System.Drawing.Point(18, 48)
$status.AutoSize = $true
$form.Controls.Add($status)

$detail = New-Object System.Windows.Forms.Label
$detail.Text = ''
$detail.Font = New-Object System.Drawing.Font('Consolas', 7.5)
$detail.ForeColor = $muted
$detail.Location = New-Object System.Drawing.Point(18, 164)
$detail.Size = New-Object System.Drawing.Size(286, 16)
$form.Controls.Add($detail)

$toggle = New-Object System.Windows.Forms.Button
$toggle.Text = 'Start'
$toggle.Font = New-Object System.Drawing.Font('Segoe UI', 10, [System.Drawing.FontStyle]::Bold)
$toggle.Size = New-Object System.Drawing.Size(284, 36)
$toggle.Location = New-Object System.Drawing.Point(18, 74)
$toggle.FlatStyle = 'Flat'
$toggle.FlatAppearance.BorderSize = 0
$toggle.BackColor = $inkSoft
$toggle.ForeColor = $paper
$toggle.Cursor = [System.Windows.Forms.Cursors]::Hand
$form.Controls.Add($toggle)

$recordLabel = New-Object System.Windows.Forms.Label
$recordLabel.Text = 'Record'
$recordLabel.Font = New-Object System.Drawing.Font('Segoe UI', 8.5)
$recordLabel.ForeColor = $muted
$recordLabel.Location = New-Object System.Drawing.Point(18, 122)
$recordLabel.AutoSize = $true
$form.Controls.Add($recordLabel)

$recordBtn = New-Object System.Windows.Forms.Button
$recordBtn.Text = 'Off'
$recordBtn.Font = New-Object System.Drawing.Font('Segoe UI', 8.5, [System.Drawing.FontStyle]::Bold)
$recordBtn.Size = New-Object System.Drawing.Size(58, 24)
$recordBtn.Location = New-Object System.Drawing.Point(72, 117)
$recordBtn.FlatStyle = 'Flat'
$recordBtn.FlatAppearance.BorderSize = 0
$recordBtn.BackColor = $inkSoft
$recordBtn.ForeColor = $muted
$recordBtn.Cursor = [System.Windows.Forms.Cursors]::Hand
$recordBtn.ShowCellToolTips = $true
$form.Controls.Add($recordBtn)

$folderLbl = New-Object System.Windows.Forms.Label
$folderLbl.Text = ''
$folderLbl.Font = New-Object System.Drawing.Font('Segoe UI', 7.5)
$folderLbl.ForeColor = $muted
$folderLbl.Location = New-Object System.Drawing.Point(138, 122)
$folderLbl.AutoSize = $true
$folderLbl.Cursor = [System.Windows.Forms.Cursors]::Hand
$form.Controls.Add($folderLbl)

$refresh = {
    $proc = Get-BotProcess
    if ($proc) {
        $up = (Get-Date) - $proc.CreationDate
        $uptime = if ($up.TotalHours -ge 1) { '{0}h{1:d2}' -f [int]$up.TotalHours, $up.Minutes } else { '{0}m{1:d2}s' -f $up.Minutes, $up.Seconds }
        $dot.BackColor = $green
        $status.Text = 'Running for ' + $uptime
        $toggle.Text = 'Stop'
        $toggle.FlatAppearance.MouseOverBackColor = $red
    } else {
        $dot.BackColor = $grey
        $status.Text = 'Stopped'
        $toggle.Text = 'Start'
        $toggle.FlatAppearance.MouseOverBackColor = $green
    }
    $detail.Text = Get-Detail $proc
    if ($tray) { $tray.Text = 'BOGOS bot: ' + $status.Text }

    $rec = Get-Recording
    if ($rec.enabled) {
        $recordBtn.Text = 'On'
        $recordBtn.ForeColor = $green
    } else {
        $recordBtn.Text = 'Off'
        $recordBtn.ForeColor = $muted
    }
    if ($rec.folder) {
        $tail = Split-Path $rec.folder -Leaf
        $head = Split-Path (Split-Path $rec.folder -Parent) -Leaf
        $shown = if ($head) { $head + '\' + $tail } else { $tail }
        if ($shown.Length -gt 26) { $shown = '...' + $shown.Substring($shown.Length - 23) }
        $folderLbl.Text = $shown
    } else {
        $folderLbl.Text = 'choose folder...'
    }
    $tip = 'Audio on disk: ' + $(if ($rec.enabled) { 'ON' } else { 'OFF' })
    if ($rec.folder) { $tip = $tip + ' under ' + $rec.folder }
    $tip = $tip + '. Applies at the next !transcript session.'
    $recordBtn.ToolTipText = $tip
    $folderLbl.ToolTipText = $tip
}

$recordBtn.Add_Click({
    $rec = Get-Recording
    if ($rec.enabled) {
        Set-Recording $false $rec.folder
    } else {
        $folder = $rec.folder
        if (-not $folder) {
            $folder = Choose-Folder
            if (-not $folder) { return }
        }
        Set-Recording $true $folder
    }
    & $refresh
})

$folderLbl.Add_Click({
    $folder = Choose-Folder
    if (-not $folder) { return }
    $rec = Get-Recording
    Set-Recording $rec.enabled $folder
    & $refresh
})

$toggle.Add_Click({
    $toggle.Enabled = $false
    if (Get-BotProcess) { Stop-Bot } else { Start-Bot }
    Start-Sleep -Milliseconds 700
    $toggle.Enabled = $true
    & $refresh
})

$close.Add_Click({ Save-Position; $form.Close() })
$form.Add_FormClosing({ Save-Position })

$drag = $false
$grab = New-Object System.Drawing.Point(0, 0)
$startDrag = { param($sender, $e) $script:drag = $true; $script:grab = $e.Location }
$doDrag = { param($sender, $e) if ($script:drag) { $form.Location = New-Object System.Drawing.Point(($form.Location.X - $script:grab.X + $e.X), ($form.Location.Y - $script:grab.Y + $e.Y)) } }
$endDrag = { param($sender, $e) $script:drag = $false }
foreach ($c in @($form, $title, $status, $dot)) {
    $c.Add_MouseDown($startDrag)
    $c.Add_MouseMove($doDrag)
    $c.Add_MouseUp($endDrag)
}

# No taskbar entry, so minimising must leave a way back. The tray icon is it.
$show = {
    $form.Show()
    $form.WindowState = 'Normal'
    $form.Activate()
}

$tray = New-Object System.Windows.Forms.NotifyIcon
try {
    $tray.Icon = [System.Drawing.Icon]::ExtractAssociatedIcon("$env:LOCALAPPDATA\Discord\Update.exe")
} catch {
    $tray.Icon = [System.Drawing.SystemIcons]::Application
}
$tray.Text = 'BOGOS bot'
$tray.Visible = $true
$tray.Add_DoubleClick($show)

$menu = New-Object System.Windows.Forms.ContextMenuStrip
$miShow = $menu.Items.Add('Show')
$miShow.Add_Click($show)
$miStart = $menu.Items.Add('Start')
$miStart.Add_Click({ Start-Bot; & $refresh })
$miStop = $menu.Items.Add('Stop')
$miStop.Add_Click({ Stop-Bot; & $refresh })
$null = $menu.Items.Add('-')
$miExit = $menu.Items.Add('Quit')
$miExit.Add_Click({ Save-Position; $tray.Visible = $false; $form.Close() })
$tray.ContextMenuStrip = $menu

$minimise.Add_Click({ $form.Hide() })

$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 1500
$timer.Add_Tick($refresh)
$timer.Start()

& $refresh
# Application::Run, not ShowDialog. ShowDialog runs a MODAL loop, and Hide()
# ends a modal loop, so minimising fell straight through to the cleanup, disposed
# the tray icon and killed the window. Application::Run keeps the message loop
# alive while the form is hidden, which is what minimising to the tray needs.
[System.Windows.Forms.Application]::Run($form)
$timer.Stop()
$tray.Visible = $false
$tray.Dispose()
