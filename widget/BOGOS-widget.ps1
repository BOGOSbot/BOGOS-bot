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
# The bot reads its Discord token and its model keys from here. The widget
# writes one line of it and leaves every other line exactly as it found it.
# .gitignore keeps it out of the repository; .env.example is the template.
$envFile = Join-Path $root '.env'

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
$H = 190
$PAD = 14
$RAIL_H = 30
$WELL_Y = 34
$WELL_H = 58
$KEY_Y = 98
$KEY_W = 212
$KEY_H = 32
$DEST_Y = 133
$API_Y = 150
$LOG_Y = 167

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

# --- the environment file ---------------------------------------------------
# That file is hand-edited, so it is read and rewritten line by line rather
# than regenerated: comments, blank lines and every other key survive a save.


function Read-EnvText {
    if (Test-Path $envFile) { return [System.IO.File]::ReadAllText($envFile) }
    # A fresh clone has the example and no .env. Seeding from it is what a
    # person would do by hand, and it leaves the token line there to fill in.
    $example = Join-Path $root '.env.example'
    if (Test-Path $example) { return [System.IO.File]::ReadAllText($example) }
    return ''
}

function Get-EnvValue($name) {
    $pattern = '^\s*' + [regex]::Escape($name) + '\s*=\s*(.*)$'
    foreach ($line in ((Read-EnvText) -split "[\r\n]+")) {
        if ($line -match $pattern) {
            $value = $Matches[1].Trim()
            if ($value.Length -ge 2) {
                $first = $value.Substring(0, 1)
                $last = $value.Substring($value.Length - 1, 1)
                if (($first -eq [string][char]34 -and $last -eq [string][char]34) -or
                    ($first -eq [string][char]39 -and $last -eq [string][char]39)) {
                    $value = $value.Substring(1, $value.Length - 2)
                }
            }
            return $value
        }
    }
    return ''
}

function Format-EnvValue($value) {
    # dotenv reads a bare value only up to the first space or comment marker.
    if ($value -match '[#\s]') { return [string][char]34 + $value + [string][char]34 }
    return $value
}

function Set-EnvValue($name, $value) {
    $text = Read-EnvText
    $crlf = [string][char]13 + [string][char]10
    $nl = if ($text.Contains($crlf)) { $crlf } else { [string][char]10 }

    $lines = New-Object System.Collections.Generic.List[string]
    if ($text.Length -gt 0) {
        foreach ($piece in ($text -split [string][char]10)) {
            $lines.Add($piece.TrimEnd([char]13))
        }
        # A trailing newline leaves one empty element; drop it, or the file
        # grows a blank line every time a key is saved.
        if ($lines.Count -gt 0 -and $lines[$lines.Count - 1] -eq '') {
            $lines.RemoveAt($lines.Count - 1)
        }
    }

    $pattern = '^\s*' + [regex]::Escape($name) + '\s*='
    $replaced = $false
    for ($i = 0; $i -lt $lines.Count; $i++) {
        if ($lines[$i] -match $pattern) {
            $lines[$i] = $name + '=' + (Format-EnvValue $value)
            $replaced = $true
            break
        }
    }
    if (-not $replaced) {
        if ($lines.Count -gt 0 -and $lines[$lines.Count - 1].Trim() -ne '') { $lines.Add('') }
        $lines.Add('# Written by the widget.')
        $lines.Add($name + '=' + (Format-EnvValue $value))
    }

    try {
        [System.IO.File]::WriteAllText(
            $envFile, ($lines -join $nl) + $nl, (New-Object System.Text.UTF8Encoding($false))
        )
        return $true
    } catch {
        [System.Windows.Forms.MessageBox]::Show(
            'Could not write .env: ' + $_.Exception.Message, 'BOGOS bot'
        ) | Out-Null
        return $false
    }
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

$apiRow = New-Object System.Windows.Forms.Button
$apiRow.Text = 'add an OpenRouter key'
$apiRow.Font = New-UiFont 8
$apiRow.Size = New-Object System.Drawing.Size(($W - 2 * $PAD + 6), 15)
$apiRow.Location = New-Object System.Drawing.Point(($PAD - 3), $API_Y)
$apiRow.FlatStyle = 'Flat'
$apiRow.FlatAppearance.BorderSize = 0
$apiRow.FlatAppearance.MouseOverBackColor = $case
$apiRow.FlatAppearance.MouseDownBackColor = $case
$apiRow.UseVisualStyleBackColor = $false
$apiRow.BackColor = $case
$apiRow.ForeColor = $ivoryDim
$apiRow.TextAlign = 'MiddleLeft'
$apiRow.AutoEllipsis = $true
$apiRow.Cursor = [System.Windows.Forms.Cursors]::Hand
$apiRow.TabIndex = 3
$form.Controls.Add($apiRow)

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

# --- the key dialog ---------------------------------------------------------
# A modal of the same case, not a system dialog: it is the one place a secret
# is typed, and it should look like part of the thing it configures.

function New-DialogButton($text, $x, $y, $w) {
    $button = New-Object System.Windows.Forms.Button
    $button.Text = $text
    $button.Font = New-DisplayFont 10
    $button.Size = New-Object System.Drawing.Size($w, 32)
    $button.Location = New-Object System.Drawing.Point($x, $y)
    $button.FlatStyle = 'Flat'
    $button.FlatAppearance.BorderSize = 1
    $button.FlatAppearance.BorderColor = $hair
    $button.FlatAppearance.MouseOverBackColor = $keyFaceHover
    $button.FlatAppearance.MouseDownBackColor = $keyFaceDown
    $button.UseVisualStyleBackColor = $false
    $button.BackColor = $keyFace
    $button.ForeColor = $ivory
    $button.Cursor = [System.Windows.Forms.Cursors]::Hand
    return $button
}

function Show-KeyDialog {
    $dlgW = 440
    $dlgH = 250

    $dlg = New-Object System.Windows.Forms.Form
    $dlg.FormBorderStyle = 'None'
    $dlg.StartPosition = 'CenterParent'
    $dlg.Size = New-Object System.Drawing.Size($dlgW, $dlgH)
    $dlg.BackColor = $case
    $dlg.ShowInTaskbar = $false
    $dlg.KeyPreview = $true
    $dlg.Add_Paint({
        param($sender, $e)
        $pen = New-Object System.Drawing.Pen($hair, 1)
        $e.Graphics.DrawRectangle($pen, 0, 0, $sender.Width - 1, $sender.Height - 1)
        $pen.Dispose()
    })

    $head = New-Object System.Windows.Forms.Label
    $head.Text = 'OpenRouter key'
    $head.Font = New-DisplayFont 12
    $head.ForeColor = $ivory
    $head.BackColor = $case
    $head.Location = New-Object System.Drawing.Point(20, 12)
    $head.AutoSize = $true
    $dlg.Controls.Add($head)

    $seam = New-Object System.Windows.Forms.Panel
    $seam.Location = New-Object System.Drawing.Point(0, 42)
    $seam.Size = New-Object System.Drawing.Size($dlgW, 1)
    $seam.BackColor = $hair
    $dlg.Controls.Add($seam)

    $body = New-Object System.Windows.Forms.Label
    $body.Text = ('Transcribing in the cloud sends every chunk to OpenRouter, ' +
                  'which needs a key. It is saved in .env on this machine and ' +
                  'is not sent anywhere else.')
    $body.Font = New-UiFont 8.5
    $body.ForeColor = $screenInk
    $body.BackColor = $case
    $body.Location = New-Object System.Drawing.Point(20, 54)
    $body.Size = New-Object System.Drawing.Size(($dlgW - 40), 40)
    $dlg.Controls.Add($body)

    $link = New-Object System.Windows.Forms.LinkLabel
    $link.Text = 'Get one at openrouter.ai/keys'
    $link.Font = New-UiFont 8.5
    # Not the lamp's amber: on this panel that colour means "the bot is
    # working", and a link is not that. The underline is the affordance.
    $link.LinkColor = $ivoryDim
    $link.ActiveLinkColor = $ivory
    $link.VisitedLinkColor = $ivoryDim
    $link.LinkBehavior = 'AlwaysUnderline'
    $link.BackColor = $case
    $link.Location = New-Object System.Drawing.Point(20, 96)
    $link.AutoSize = $true
    $link.Add_LinkClicked({
        try { Start-Process 'https://openrouter.ai/keys' } catch { }
    })
    $dlg.Controls.Add($link)

    $box = New-Object System.Windows.Forms.TextBox
    $box.Text = Get-EnvValue 'OPENROUTER_API_KEY'
    $box.Font = New-UiFont 10
    $box.Location = New-Object System.Drawing.Point(20, 122)
    $box.Size = New-Object System.Drawing.Size(($dlgW - 40), 26)
    $box.BackColor = $well
    $box.ForeColor = $ivory
    $box.BorderStyle = 'FixedSingle'
    # Masked by default: the dialog is the one place the key is on screen, and
    # a shoulder is cheaper to defend against than a screenshot.
    $box.UseSystemPasswordChar = $true
    $box.TabIndex = 0
    $dlg.Controls.Add($box)

    $reveal = New-Object System.Windows.Forms.CheckBox
    $reveal.Text = 'show it'
    $reveal.Font = New-UiFont 8.5
    $reveal.ForeColor = $screenInk
    $reveal.BackColor = $case
    $reveal.FlatStyle = 'Flat'
    $reveal.Location = New-Object System.Drawing.Point(20, 156)
    $reveal.AutoSize = $true
    $reveal.Add_CheckedChanged({ $box.UseSystemPasswordChar = -not $reveal.Checked })
    $dlg.Controls.Add($reveal)

    $save = New-DialogButton 'SAVE' 20 196 140
    $save.TabIndex = 1
    $save.Add_Click({ Set-EnvValue 'OPENROUTER_API_KEY' $box.Text.Trim(); $dlg.Close() })
    $dlg.Controls.Add($save)

    $clear = New-DialogButton 'CLEAR' 168 196 110
    $clear.TabIndex = 2
    $clear.Add_Click({ Set-EnvValue 'OPENROUTER_API_KEY' ''; $dlg.Close() })
    $dlg.Controls.Add($clear)

    $cancel = New-DialogButton 'CANCEL' 286 196 134
    $cancel.TabIndex = 3
    $cancel.Add_Click({ $dlg.Close() })
    $dlg.Controls.Add($cancel)

    # Focus lands in the box. Put the caret at the end rather than selecting
    # the whole key, which paints the one bright system colour in the dialog.
    $dlg.Add_Shown({ $box.SelectionStart = $box.Text.Length; $box.SelectionLength = 0 })

    $dlg.AcceptButton = $save
    $dlg.Add_KeyDown({
        param($sender, $e)
        if ($e.KeyCode -eq [System.Windows.Forms.Keys]::Escape) { $dlg.Close() }
    })

    $dragging = $false
    $offset = New-Object System.Drawing.Point(0, 0)
    foreach ($c in @($dlg, $head, $seam, $body)) {
        $c.Add_MouseDown({ param($sender, $e) $script:dragging = $true; $script:offset = $e.Location })
        $c.Add_MouseMove({
            param($sender, $e)
            if ($script:dragging) {
                $dlg.Location = New-Object System.Drawing.Point(
                    ($dlg.Location.X - $script:offset.X + $e.X),
                    ($dlg.Location.Y - $script:offset.Y + $e.Y))
            }
        })
        $c.Add_MouseUp({ param($sender, $e) $script:dragging = $false })
    }

    $dlg.ShowDialog($form) | Out-Null
    $dlg.Dispose()
    Update-Panel
}

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

    if (Get-EnvValue 'OPENROUTER_API_KEY') {
        $apiRow.Text = 'OpenRouter key set'
    } else {
        $apiRow.Text = 'add an OpenRouter key'
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
    $tipProvider.SetToolTip($apiRow, 'The key cloud transcription uses. Saved in .env on this machine.')
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

$apiRow.Add_MouseEnter({ $apiRow.ForeColor = $ivory })
$apiRow.Add_MouseLeave({ $apiRow.ForeColor = $ivoryDim })
$apiRow.Add_Click({ Show-KeyDialog })

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
