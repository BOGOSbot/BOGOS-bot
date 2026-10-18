# BOGOS bot widget.
#
# The front panel of the recorder: one recessed readout that says what the bot
# is doing, one key that starts and stops it, one latch that decides whether the
# voices are kept. Every value on the panel is read from a file the bot writes,
# so the widget is a reader and never a second source of truth.
#
# Windows 10 has no widget board, and the Windows 11 one needs an MSIX-packaged
# provider published through the Store, so a floating panel is the closest thing
# that works locally.
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
# The bot publishes what it is doing; the widget only reads it. A file rather
# than an endpoint because both run on this machine and neither should need a
# port open to know about the other.
$statusFile = Join-Path $root 'status.json'

# Glyphs built from code points rather than typed into the file: Windows
# PowerShell reads a .ps1 as ANSI unless it carries a byte-order mark, and these
# would come back mangled.
$dash = [string][char]0x2013
$cross = [string][char]0x00D7
$ellipsis = [string][char]0x2026

# --- type -------------------------------------------------------------------
# Bahnschrift is the DIN-derived face Windows ships for technical readouts. It
# arrived in Windows 10 1709, so fall back to the UI face where it is missing.
$displayFace = 'Segoe UI'
$displayStyle = [System.Drawing.FontStyle]::Bold
try {
    $families = @((New-Object System.Drawing.Text.InstalledFontCollection).Families |
                  ForEach-Object { $_.Name })
    if ($families -contains 'Bahnschrift SemiBold') {
        $displayFace = 'Bahnschrift SemiBold'
        $displayStyle = [System.Drawing.FontStyle]::Regular
    }
} catch { }

function New-DisplayFont($size) {
    return New-Object System.Drawing.Font($displayFace, $size, $displayStyle)
}

function New-UiFont($size) {
    return New-Object System.Drawing.Font('Segoe UI', $size)
}

# --- the case ---------------------------------------------------------------
# Warm graphite, not a neutral near-black: the panel is meant to read as a piece
# of equipment standing on the desktop rather than as a dark card. The only two
# colours that carry meaning are the lamps.
$case = [System.Drawing.Color]::FromArgb(27, 26, 23)
$well = [System.Drawing.Color]::FromArgb(18, 17, 16)
$hair = [System.Drawing.Color]::FromArgb(53, 50, 44)
$screenInk = [System.Drawing.Color]::FromArgb(142, 135, 124)
$ivory = [System.Drawing.Color]::FromArgb(237, 231, 219)
$ivoryBright = [System.Drawing.Color]::FromArgb(255, 250, 240)
$ivoryDim = [System.Drawing.Color]::FromArgb(181, 174, 162)
$amber = [System.Drawing.Color]::FromArgb(233, 161, 59)
$lampOff = [System.Drawing.Color]::FromArgb(58, 54, 47)
$recRed = [System.Drawing.Color]::FromArgb(208, 72, 58)
$recOff = [System.Drawing.Color]::FromArgb(74, 59, 55)
$keyFace = [System.Drawing.Color]::FromArgb(42, 39, 35)
$keyFaceHover = [System.Drawing.Color]::FromArgb(51, 47, 41)
$keyFaceDown = [System.Drawing.Color]::FromArgb(36, 33, 30)

# --- the grid ---------------------------------------------------------------
# 4px units. 352x176 is wide and short like a rack unit: it reads as gear and
# covers less of whatever is behind it than a square card would.
$W = 352
$H = 176
$PAD = 14
$RAIL_H = 30
$WELL_Y = 34
$WELL_H = 58
$KEY_Y = 98
$KEY_W = 212
$KEY_H = 32
$DEST_Y = 136
$LOG_Y = 153

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

function Get-BotState($proc) {
    # The bot's own names (sleeping, emptying, reconciling) describe its parts.
    # The panel says what a person would say about the same moment.
    $map = @{
        sleeping    = @{ word = 'IDLE';         sub = 'waiting for !transcript' }
        listening   = @{ word = 'CAPTURING';    sub = 'in the voice channel' }
        emptying    = @{ word = 'TRANSCRIBING'; sub = 'working through the queue' }
        reconciling = @{ word = 'WRITING';      sub = 'assembling the transcript' }
    }
    if (-not $proc) { return $null }
    $unknown = @{ word = 'STARTING'; sub = 'waiting for the bot to report'; busy = $false }
    if (-not (Test-Path $statusFile)) { return $unknown }
    try {
        # A status file older than the process is a leftover from an earlier run,
        # so the bot has not reported yet and the panel must not claim otherwise.
        if ((Get-Item $statusFile).LastWriteTime -lt $proc.CreationDate) { return $unknown }
        $state = [string](Get-Content $statusFile -Raw -Encoding UTF8 | ConvertFrom-Json).state
        if (-not $map.ContainsKey($state)) { return $unknown }
        $entry = $map[$state]
        return @{ word = $entry.word; sub = $entry.sub; busy = ($state -ne 'sleeping') }
    } catch {
        return $unknown
    }
}

function Get-LastLogLine($proc) {
    # The widget only has a log for bots it started itself. One launched from the
    # desktop shortcut logs to its own console, so say that rather than showing a
    # stale line from an older run.
    if (-not (Test-Path $logFile)) { return 'started outside the widget' }
    if ($proc -and (Get-Item $logFile).LastWriteTime -lt $proc.CreationDate) {
        return 'started outside the widget'
    }
    $line = Get-Content $logFile -Tail 1 -Encoding UTF8 -ErrorAction SilentlyContinue
    if (-not $line) { return 'log is empty' }
    # Strip the timestamp and level so the panel shows the message.
    $trimmed = ($line -split [char]0x2502)[-1].Trim()
    if (-not $trimmed) { $trimmed = $line.Trim() }
    return $trimmed
}

function Format-Uptime($start) {
    $span = (Get-Date) - $start
    if ($span.TotalSeconds -lt 0) { $span = [System.TimeSpan]::Zero }
    if ($span.TotalHours -ge 1) {
        return ('{0}:{1:d2}:{2:d2}' -f [int]$span.TotalHours, $span.Minutes, $span.Seconds)
    }
    return ('{0:d2}:{1:d2}' -f $span.Minutes, $span.Seconds)
}

function Shorten-Path($path, $budget) {
    # Keep the drive and the folder's own name: which disk it lands on and what
    # it is called are the two parts a person actually reads.
    if ($path.Length -le $budget) { return $path }
    $drive = Split-Path $path -Qualifier
    $leaf = Split-Path $path -Leaf
    $short = "$drive\$ellipsis\$leaf"
    if ($short.Length -le $budget) { return $short }
    $room = $budget - $drive.Length - 3
    if ($room -lt 4) { return $short.Substring(0, $budget) }
    return "$drive\$ellipsis\" + $leaf.Substring($leaf.Length - $room)
}

function Get-Recording {
    if (-not (Test-Path $recordingFile)) { return @{ enabled = $false; folder = '' } }
    try {
        $d = Get-Content $recordingFile -Raw -Encoding UTF8 | ConvertFrom-Json
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

function Load-Position {
    if (Test-Path $stateFile) {
        try {
            $s = Get-Content $stateFile -Raw | ConvertFrom-Json
            return New-Object System.Drawing.Point([int]$s.X, [int]$s.Y)
        } catch { }
    }
    $screen = [System.Windows.Forms.Screen]::PrimaryScreen.WorkingArea
    return New-Object System.Drawing.Point(($screen.Right - ($W + 20)), 60)
}

function Save-Position {
    try {
        @{ X = $form.Location.X; Y = $form.Location.Y } |
            ConvertTo-Json | Set-Content -Path $stateFile -Encoding UTF8
    } catch { }
}

function New-Lamp($x, $y, $diameter, $fill) {
    # A lamp is a circle, not a coloured label: the shape is what makes it read
    # as a light on the case rather than as punctuation in the text.
    $lamp = New-Object System.Windows.Forms.Panel
    $lamp.Location = New-Object System.Drawing.Point($x, $y)
    $lamp.Size = New-Object System.Drawing.Size($diameter, $diameter)
    $lamp.Tag = $fill.ToArgb()
    $lamp.Add_Paint({
        param($sender, $e)
        $e.Graphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
        $brush = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb([int]$sender.Tag))
        $e.Graphics.FillEllipse($brush, 0, 0, $sender.Width - 1, $sender.Height - 1)
        $brush.Dispose()
    })
    return $lamp
}

function Set-Lamp($lamp, $fill) {
    $lamp.Tag = $fill.ToArgb()
    $lamp.Invalidate()
}

function New-TrayIcon($fill, $ring) {
    # The tray is the panel's second face: minimising must not cost the one bit
    # that matters, which is whether anything is being captured.
    $bmp = New-Object System.Drawing.Bitmap(16, 16)
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
    $g.Clear([System.Drawing.Color]::Transparent)
    if ($fill.ToArgb() -ne 0) {
        $brush = New-Object System.Drawing.SolidBrush($fill)
        $g.FillEllipse($brush, 2, 2, 11, 11)
        $brush.Dispose()
    }
    $pen = New-Object System.Drawing.Pen($ring, 1.5)
    $g.DrawEllipse($pen, 2, 2, 11, 11)
    $pen.Dispose()
    $g.Dispose()
    $handle = $bmp.GetHicon()
    $bmp.Dispose()
    return [System.Drawing.Icon]::FromHandle($handle)
}

# --- the panel --------------------------------------------------------------
$form = New-Object System.Windows.Forms.Form
$form.FormBorderStyle = 'None'
$form.StartPosition = 'Manual'
$form.Location = Load-Position
$form.Size = New-Object System.Drawing.Size($W, $H)
# Not topmost: the panel should fall behind other windows like anything else.
$form.TopMost = $false
$form.ShowInTaskbar = $false
$form.BackColor = $case
$form.Add_Paint({
    param($sender, $e)
    # A hard 1px edge, no rounding and no shadow. It is the edge of a case.
    $pen = New-Object System.Drawing.Pen($hair, 1)
    $e.Graphics.DrawRectangle($pen, 0, 0, $sender.Width - 1, $sender.Height - 1)
    $pen.Dispose()
})

$identity = New-Object System.Windows.Forms.Label
$identity.Text = 'BOGOS'
$identity.Font = New-DisplayFont 12
$identity.ForeColor = $ivory
$identity.BackColor = $case
$identity.Location = New-Object System.Drawing.Point($PAD, 8)
$identity.AutoSize = $true
$form.Controls.Add($identity)

# The seam between what this thing is and what it is doing.
$seam = New-Object System.Windows.Forms.Panel
$seam.Location = New-Object System.Drawing.Point(0, $RAIL_H)
$seam.Size = New-Object System.Drawing.Size($W, 1)
$seam.BackColor = $hair
$form.Controls.Add($seam)

$minimise = New-Object System.Windows.Forms.Label
$minimise.Text = $dash
$minimise.Font = New-UiFont 10
$minimise.ForeColor = $screenInk
$minimise.BackColor = $case
$minimise.Location = New-Object System.Drawing.Point(($W - $PAD - 46), 4)
$minimise.Size = New-Object System.Drawing.Size(22, 22)
$minimise.TextAlign = 'MiddleCenter'
$minimise.Cursor = [System.Windows.Forms.Cursors]::Hand
$form.Controls.Add($minimise)

$close = New-Object System.Windows.Forms.Label
$close.Text = $cross
$close.Font = New-UiFont 10
$close.ForeColor = $screenInk
$close.BackColor = $case
$close.Location = New-Object System.Drawing.Point(($W - $PAD - 24), 4)
$close.Size = New-Object System.Drawing.Size(22, 22)
$close.TextAlign = 'MiddleCenter'
$close.Cursor = [System.Windows.Forms.Cursors]::Hand
$form.Controls.Add($close)

# The readout is recessed, not raised. A well says "a window into the machine";
# a raised card says "a panel from a dashboard", which is the thing to avoid.
$wellPanel = New-Object System.Windows.Forms.Panel
$wellPanel.Location = New-Object System.Drawing.Point($PAD, $WELL_Y)
$wellPanel.Size = New-Object System.Drawing.Size(($W - 2 * $PAD), $WELL_H)
$wellPanel.BackColor = $well
$wellPanel.Add_Paint({
    param($sender, $e)
    $pen = New-Object System.Drawing.Pen($hair, 1)
    $e.Graphics.DrawRectangle($pen, 0, 0, $sender.Width - 1, $sender.Height - 1)
    $pen.Dispose()
})
$form.Controls.Add($wellPanel)

$stateLamp = New-Lamp 10 16 9 $lampOff
$stateLamp.BackColor = $well
$wellPanel.Controls.Add($stateLamp)

$stateWord = New-Object System.Windows.Forms.Label
$stateWord.Text = 'STARTING'
$stateWord.Font = New-DisplayFont 15
$stateWord.ForeColor = $ivory
$stateWord.BackColor = $well
$stateWord.Location = New-Object System.Drawing.Point(32, 8)
$stateWord.AutoSize = $true
$wellPanel.Controls.Add($stateWord)

$subLine = New-Object System.Windows.Forms.Label
$subLine.Text = ''
$subLine.Font = New-UiFont 8.5
$subLine.ForeColor = $screenInk
$subLine.BackColor = $well
$subLine.Location = New-Object System.Drawing.Point(32, 34)
$subLine.AutoSize = $true
$wellPanel.Controls.Add($subLine)

$uptime = New-Object System.Windows.Forms.Label
$uptime.Text = ''
$uptime.Font = New-DisplayFont 11.5
$uptime.ForeColor = $screenInk
$uptime.BackColor = $well
$uptime.Location = New-Object System.Drawing.Point(($wellPanel.Width - 122), 13)
$uptime.Size = New-Object System.Drawing.Size(110, 18)
$uptime.TextAlign = 'MiddleRight'
$wellPanel.Controls.Add($uptime)

$keyBtn = New-Object System.Windows.Forms.Button
$keyBtn.Text = 'START'
$keyBtn.Font = New-DisplayFont 11
$keyBtn.Size = New-Object System.Drawing.Size($KEY_W, $KEY_H)
$keyBtn.Location = New-Object System.Drawing.Point($PAD, $KEY_Y)
$keyBtn.FlatStyle = 'Flat'
$keyBtn.FlatAppearance.BorderSize = 1
$keyBtn.FlatAppearance.BorderColor = $hair
$keyBtn.FlatAppearance.MouseOverBackColor = $keyFaceHover
$keyBtn.FlatAppearance.MouseDownBackColor = $keyFaceDown
$keyBtn.UseVisualStyleBackColor = $false
$keyBtn.BackColor = $keyFace
$keyBtn.ForeColor = $ivory
$keyBtn.Cursor = [System.Windows.Forms.Cursors]::Hand
$keyBtn.TabIndex = 0
$form.Controls.Add($keyBtn)

# A printed word beside a lamp, the way a recorder labels its own switch: the
# word names the function, the lamp answers whether it is on. Colour is never
# the only signal, because the line underneath says it in words too.
$recLamp = New-Lamp ($KEY_W + $PAD + 22) ($KEY_Y + 12) 9 $recOff
$recLamp.BackColor = $case
$form.Controls.Add($recLamp)

$recWord = New-Object System.Windows.Forms.Button
$recWord.Text = 'REC'
$recWord.Font = New-UiFont 8.5
$recWord.Size = New-Object System.Drawing.Size(52, 24)
$recWord.Location = New-Object System.Drawing.Point(($KEY_W + $PAD + 37), ($KEY_Y + 4))
$recWord.FlatStyle = 'Flat'
$recWord.FlatAppearance.BorderSize = 0
$recWord.FlatAppearance.MouseOverBackColor = $case
$recWord.FlatAppearance.MouseDownBackColor = $case
$recWord.UseVisualStyleBackColor = $false
$recWord.BackColor = $case
$recWord.ForeColor = $screenInk
$recWord.TextAlign = 'MiddleLeft'
$recWord.Cursor = [System.Windows.Forms.Cursors]::Hand
$recWord.TabIndex = 1
$form.Controls.Add($recWord)

$dest = New-Object System.Windows.Forms.Button
$dest.Text = 'choose a folder'
$dest.Font = New-UiFont 8
$dest.Size = New-Object System.Drawing.Size(($W - 2 * $PAD + 6), 15)
$dest.Location = New-Object System.Drawing.Point(($PAD - 3), $DEST_Y)
$dest.FlatStyle = 'Flat'
$dest.FlatAppearance.BorderSize = 0
$dest.FlatAppearance.MouseOverBackColor = $case
$dest.FlatAppearance.MouseDownBackColor = $case
$dest.UseVisualStyleBackColor = $false
$dest.BackColor = $case
$dest.ForeColor = $ivoryDim
$dest.TextAlign = 'MiddleLeft'
$dest.AutoEllipsis = $true
$dest.Cursor = [System.Windows.Forms.Cursors]::Hand
$dest.TabIndex = 2
$form.Controls.Add($dest)

$logLine = New-Object System.Windows.Forms.Label
$logLine.Text = ''
$logLine.Font = New-UiFont 7.5
$logLine.ForeColor = $screenInk
$logLine.BackColor = $case
$logLine.Location = New-Object System.Drawing.Point($PAD, $LOG_Y)
$logLine.Size = New-Object System.Drawing.Size(($W - 2 * $PAD), 15)
$logLine.AutoEllipsis = $true
$form.Controls.Add($logLine)

# ToolTip is its own component in WinForms; controls have no ToolTipText at
# runtime, only the extender adds one at design time.
$tipProvider = New-Object System.Windows.Forms.ToolTip

# --- the refresh ------------------------------------------------------------
# The tray icon is redrawn only when the state it shows actually changes: each
# one is a GDI handle, and the timer runs every 1.5 seconds.
$script:trayKey = ''

function Update-Panel {
    $proc = Get-BotProcess
    $bot = Get-BotState $proc

    if (-not $proc) {
        Set-Lamp $stateLamp $lampOff
        $stateWord.Text = 'STOPPED'
        $stateWord.ForeColor = $screenInk
        $subLine.Text = 'not running'
        $uptime.Text = ''
        $keyBtn.Text = 'START'
        $trayKey = 'stopped'
    } else {
        Set-Lamp $stateLamp $(if ($bot.busy) { $amber } else { $lampOff })
        $stateWord.Text = $bot.word
        $stateWord.ForeColor = $ivory
        $subLine.Text = $bot.sub
        $uptime.Text = 'up ' + (Format-Uptime $proc.CreationDate)
        $keyBtn.Text = 'STOP'
        $trayKey = if ($bot.busy) { 'busy' } else { 'idle' }
    }

    $logLine.Text = Get-LastLogLine $proc

    $recording = Get-Recording
    if ($recording.enabled) {
        Set-Lamp $recLamp $recRed
        $recWord.ForeColor = $recRed
        $dest.Text = Shorten-Path $recording.folder 62
    } else {
        Set-Lamp $recLamp $recOff
        $recWord.ForeColor = $screenInk
        if ($recording.folder) { $dest.Text = 'not saving to disk' } else { $dest.Text = 'choose a folder' }
    }

    $recWord.AccessibleName = 'Record audio to disk'
    $recWord.AccessibleDescription = $(if ($recording.enabled) { 'on' } else { 'off' })
    $keyBtn.AccessibleName = $keyBtn.Text + ' the bot'

    $tipState = 'The bot is not running.'
    if ($proc) { $tipState = 'The bot reports: ' + $bot.word.ToLower() + '.' }
    $tipRecording = 'Voices are not being written to disk.'
    if ($recording.enabled) { $tipRecording = 'Everyone in the channel is written to ' + $recording.folder + '.' }
    $tipProvider.SetToolTip($wellPanel, $tipState + ' ' + $tipRecording)
    $tipProvider.SetToolTip($stateWord, $tipState)
    $tipProvider.SetToolTip($subLine, $tipState)
    $tipProvider.SetToolTip($uptime, 'Time since the bot process started.')
    $tipProvider.SetToolTip($recWord, $tipRecording + ' Applies at the next !transcript session.')
    $tipProvider.SetToolTip($recLamp, $tipRecording + ' Applies at the next !transcript session.')
    $tipProvider.SetToolTip($dest, $tipRecording + ' Click to choose the folder.')
    $tipProvider.SetToolTip($keyBtn, 'Start or stop the bot process.')
    $tipProvider.SetToolTip($logLine, 'The last line the bot logged.')

    if ($trayKey -ne $script:trayKey) {
        $script:trayKey = $trayKey
        if ($trayKey -eq 'busy') {
            $tray.Icon = New-TrayIcon $amber $case
        } elseif ($trayKey -eq 'idle') {
            $tray.Icon = New-TrayIcon $screenInk $case
        } else {
            $tray.Icon = New-TrayIcon ([System.Drawing.Color]::Transparent) $screenInk
        }
    }
    if ($tray) { $tray.Text = 'BOGOS bot: ' + $stateWord.Text }
}

$keyBtn.Add_MouseEnter({ $keyBtn.ForeColor = $ivoryBright })
$keyBtn.Add_MouseLeave({ $keyBtn.ForeColor = $ivory })
$keyBtn.Add_GotFocus({ $keyBtn.FlatAppearance.BorderColor = $screenInk })
$keyBtn.Add_LostFocus({ $keyBtn.FlatAppearance.BorderColor = $hair })
$keyBtn.Add_Click({
    $keyBtn.Enabled = $false
    if (Get-BotProcess) { Stop-Bot } else { Start-Bot }
    Start-Sleep -Milliseconds 700
    $keyBtn.Enabled = $true
    Update-Panel
})

$recWord.Add_MouseEnter({ $recWord.ForeColor = $ivoryBright })
$recWord.Add_MouseLeave({ Update-Panel })
$recWord.Add_Click({
    $recording = Get-Recording
    if ($recording.enabled) {
        Set-Recording $false $recording.folder
    } else {
        $folder = $recording.folder
        if (-not $folder) {
            $folder = Choose-Folder
            if (-not $folder) { return }
        }
        Set-Recording $true $folder
    }
    Update-Panel
})

$dest.Add_MouseEnter({ $dest.ForeColor = $ivory })
$dest.Add_MouseLeave({ $dest.ForeColor = $ivoryDim })
$dest.Add_Click({
    $folder = Choose-Folder
    if (-not $folder) { return }
    $recording = Get-Recording
    Set-Recording $recording.enabled $folder
    Update-Panel
})

$close.Add_MouseEnter({ $close.ForeColor = $ivory })
$close.Add_MouseLeave({ $close.ForeColor = $screenInk })
$close.Add_Click({ Save-Position; $form.Close() })
$minimise.Add_MouseEnter({ $minimise.ForeColor = $ivory })
$minimise.Add_MouseLeave({ $minimise.ForeColor = $screenInk })
$minimise.Add_Click({ $form.Hide() })
$form.Add_FormClosing({ Save-Position })

$drag = $false
$grab = New-Object System.Drawing.Point(0, 0)
$startDrag = { param($sender, $e) $script:drag = $true; $script:grab = $e.Location }
$doDrag = { param($sender, $e) if ($script:drag) { $form.Location = New-Object System.Drawing.Point(($form.Location.X - $script:grab.X + $e.X), ($form.Location.Y - $script:grab.Y + $e.Y)) } }
$endDrag = { param($sender, $e) $script:drag = $false }
foreach ($c in @($form, $identity, $seam, $wellPanel, $stateLamp, $stateWord, $subLine, $uptime)) {
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
$tray.Icon = New-TrayIcon ([System.Drawing.Color]::Transparent) $screenInk
$tray.Text = 'BOGOS bot'
$tray.Visible = $true
$tray.Add_DoubleClick($show)

$menu = New-Object System.Windows.Forms.ContextMenuStrip
$miShow = $menu.Items.Add('Show')
$miShow.Add_Click($show)
$miStart = $menu.Items.Add('Start')
$miStart.Add_Click({ Start-Bot; Update-Panel })
$miStop = $menu.Items.Add('Stop')
$miStop.Add_Click({ Stop-Bot; Update-Panel })
$null = $menu.Items.Add('-')
$miExit = $menu.Items.Add('Quit')
$miExit.Add_Click({ Save-Position; $tray.Visible = $false; $form.Close() })
$tray.ContextMenuStrip = $menu

$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 1500
$timer.Add_Tick({ Update-Panel })
$timer.Start()

Update-Panel
# Application::Run, not ShowDialog. ShowDialog runs a MODAL loop, and Hide()
# ends a modal loop, so minimising fell straight through to the cleanup, disposed
# the tray icon and killed the window. Application::Run keeps the message loop
# alive while the form is hidden, which is what minimising to the tray needs.
[System.Windows.Forms.Application]::Run($form)
$timer.Stop()
$tray.Visible = $false
$tray.Dispose()
