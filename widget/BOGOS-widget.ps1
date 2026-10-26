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

param(
    # Dev aid: render the panel and tray marks into a folder and exit. An icon
    # is judged by looking at it, and this is how that look is produced without
    # opening the panel on top of whatever is on screen.
    [string]$PreviewDir = ''
)

# The widget runs from a hidden console, so an error that stops it would
# otherwise leave no trace at all. It goes here instead.
trap {
    $log = Join-Path $PSScriptRoot 'widget-error.log'
    Add-Content -Path $log -Value ((Get-Date -Format s) + ' ' + ($_ | Out-String))
    break
}

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()

# --- the controls -----------------------------------------------------------
# Stock WinForms draws square, flat, 1990s controls, and it cannot be styled
# out of that from PowerShell. These few owner-drawn ones are what give the
# panel its rounded key, glowing lamps and recessed well. C# 5 only: Windows
# PowerShell compiles this with the .NET Framework compiler.
if (-not ('BogosUi.PillButton' -as [type])) {
    Add-Type -ReferencedAssemblies 'System.Windows.Forms', 'System.Drawing' -TypeDefinition @'
using System;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Runtime.InteropServices;
using System.Windows.Forms;

namespace BogosUi
{
    public static class Shapes
    {
        public static GraphicsPath Round(RectangleF r, float radius)
        {
            GraphicsPath p = new GraphicsPath();
            float d = Math.Min(radius * 2f, Math.Min(r.Width, r.Height));
            if (d <= 1f) { p.AddRectangle(r); return p; }
            p.AddArc(r.X, r.Y, d, d, 180, 90);
            p.AddArc(r.Right - d, r.Y, d, d, 270, 90);
            p.AddArc(r.Right - d, r.Bottom - d, d, d, 0, 90);
            p.AddArc(r.X, r.Bottom - d, d, d, 90, 90);
            p.CloseFigure();
            return p;
        }

        public static Color Behind(Control c)
        {
            return c.Parent != null ? c.Parent.BackColor : c.BackColor;
        }
    }

    // The case: a borderless window with a drop shadow, and on Windows 11 the
    // system's own rounded corners and hairline edge.
    public class CaseForm : Form
    {
        [DllImport("dwmapi.dll")]
        static extern int DwmSetWindowAttribute(IntPtr hwnd, int attr, ref int value, int size);

        public Color EdgeColor = Color.Empty;
        public bool SystemEdge;

        public CaseForm()
        {
            DoubleBuffered = true;
        }

        protected override CreateParams CreateParams
        {
            get
            {
                CreateParams cp = base.CreateParams;
                cp.ClassStyle |= 0x20000; // CS_DROPSHADOW
                return cp;
            }
        }

        protected override void OnHandleCreated(EventArgs e)
        {
            base.OnHandleCreated(e);
            try
            {
                int round = 2; // DWMWCP_ROUND
                SystemEdge = DwmSetWindowAttribute(Handle, 33, ref round, 4) == 0;
                if (SystemEdge && !EdgeColor.IsEmpty)
                {
                    int bgr = EdgeColor.R | (EdgeColor.G << 8) | (EdgeColor.B << 16);
                    DwmSetWindowAttribute(Handle, 34, ref bgr, 4);
                }
            }
            catch (Exception) { SystemEdge = false; }
        }

        protected override void OnPaint(PaintEventArgs e)
        {
            base.OnPaint(e);
            // Before Windows 11 there are no system corners, so the case draws
            // its own hairline edge instead.
            if (!SystemEdge && !EdgeColor.IsEmpty)
            {
                using (Pen pen = new Pen(EdgeColor, 1f))
                {
                    e.Graphics.DrawRectangle(pen, 0, 0, Width - 1, Height - 1);
                }
            }
        }
    }

    // A recessed, rounded readout.
    public class Well : Panel
    {
        public Color Edge = Color.Empty;
        public float Radius = 8f;

        public Well()
        {
            SetStyle(ControlStyles.UserPaint | ControlStyles.AllPaintingInWmPaint |
                     ControlStyles.OptimizedDoubleBuffer | ControlStyles.ResizeRedraw, true);
        }

        protected override void OnPaintBackground(PaintEventArgs e) { }

        protected override void OnPaint(PaintEventArgs e)
        {
            Graphics g = e.Graphics;
            g.Clear(Shapes.Behind(this));
            g.SmoothingMode = SmoothingMode.AntiAlias;
            using (GraphicsPath p = Shapes.Round(new RectangleF(0.5f, 0.5f, Width - 1.5f, Height - 1.5f), Radius))
            {
                using (SolidBrush b = new SolidBrush(BackColor)) { g.FillPath(b, p); }
                if (!Edge.IsEmpty)
                {
                    using (Pen pen = new Pen(Edge, 1f)) { g.DrawPath(pen, p); }
                }
            }
        }
    }

    // A lamp: a dot that glows when it is lit.
    public class Lamp : Control
    {
        public Color Fill = Color.Gray;
        public bool Glow;
        public int Core = 9;

        public Lamp()
        {
            SetStyle(ControlStyles.UserPaint | ControlStyles.AllPaintingInWmPaint |
                     ControlStyles.OptimizedDoubleBuffer | ControlStyles.ResizeRedraw, true);
        }

        public void Set(Color fill, bool glow)
        {
            if (fill.ToArgb() == Fill.ToArgb() && glow == Glow) { return; }
            Fill = fill;
            Glow = glow;
            Invalidate();
        }

        protected override void OnPaint(PaintEventArgs e)
        {
            Graphics g = e.Graphics;
            g.Clear(Shapes.Behind(this));
            g.SmoothingMode = SmoothingMode.AntiAlias;
            float cx = Width / 2f, cy = Height / 2f;
            if (Glow)
            {
                float[] sizes = { Core + 8f, Core + 4f };
                int[] alphas = { 34, 64 };
                for (int i = 0; i < sizes.Length; i++)
                {
                    using (SolidBrush halo = new SolidBrush(Color.FromArgb(alphas[i], Fill)))
                    {
                        g.FillEllipse(halo, cx - sizes[i] / 2f, cy - sizes[i] / 2f, sizes[i], sizes[i]);
                    }
                }
            }
            using (SolidBrush b = new SolidBrush(Fill))
            {
                g.FillEllipse(b, cx - Core / 2f, cy - Core / 2f, Core, Core);
            }
        }
    }

    // A rounded button that keeps everything a Button does - focus, keyboard,
    // Click, accessibility - and draws itself. With no fill it is a text row.
    public class PillButton : Button
    {
        public Color Fill = Color.Empty;
        public Color FillHover = Color.Empty;
        public Color FillDown = Color.Empty;
        public Color Border = Color.Empty;
        public Color FocusBorder = Color.Empty;
        public Color HoverInk = Color.Empty;
        public Color Dot = Color.Empty;
        public bool DotGlow;
        public float Radius = 7f;
        bool hover, down;

        public PillButton()
        {
            SetStyle(ControlStyles.UserPaint | ControlStyles.AllPaintingInWmPaint |
                     ControlStyles.OptimizedDoubleBuffer | ControlStyles.ResizeRedraw, true);
            FlatStyle = FlatStyle.Flat;
            FlatAppearance.BorderSize = 0;
            UseVisualStyleBackColor = false;
            Cursor = Cursors.Hand;
        }

        protected override void OnMouseEnter(EventArgs e) { hover = true; Invalidate(); base.OnMouseEnter(e); }
        protected override void OnMouseLeave(EventArgs e) { hover = false; down = false; Invalidate(); base.OnMouseLeave(e); }
        protected override void OnMouseDown(MouseEventArgs e) { if (e.Button == MouseButtons.Left) { down = true; Invalidate(); } base.OnMouseDown(e); }
        protected override void OnMouseUp(MouseEventArgs e) { down = false; Invalidate(); base.OnMouseUp(e); }
        protected override void OnGotFocus(EventArgs e) { Invalidate(); base.OnGotFocus(e); }
        protected override void OnLostFocus(EventArgs e) { Invalidate(); base.OnLostFocus(e); }
        protected override void OnPaintBackground(PaintEventArgs e) { }

        protected override void OnPaint(PaintEventArgs e)
        {
            Graphics g = e.Graphics;
            g.Clear(Shapes.Behind(this));
            g.SmoothingMode = SmoothingMode.AntiAlias;

            Color fill = Fill;
            if (down && !FillDown.IsEmpty) { fill = FillDown; }
            else if (hover && !FillHover.IsEmpty) { fill = FillHover; }

            RectangleF r = new RectangleF(0.5f, 0.5f, Width - 1.5f, Height - 1.5f);
            using (GraphicsPath p = Shapes.Round(r, Radius))
            {
                if (!fill.IsEmpty && fill.A > 0)
                {
                    using (SolidBrush b = new SolidBrush(fill)) { g.FillPath(b, p); }
                }
                Color edge = Border;
                if (Focused && ShowFocusCues && !FocusBorder.IsEmpty) { edge = FocusBorder; }
                if (!edge.IsEmpty && edge.A > 0)
                {
                    using (Pen pen = new Pen(edge, 1f)) { g.DrawPath(pen, p); }
                }
            }

            int x = Padding.Left;
            if (!Dot.IsEmpty)
            {
                float core = 8f;
                float cy = Height / 2f;
                float cx = x + core / 2f + 2f;
                if (DotGlow)
                {
                    using (SolidBrush halo = new SolidBrush(Color.FromArgb(56, Dot)))
                    {
                        g.FillEllipse(halo, cx - (core + 6f) / 2f, cy - (core + 6f) / 2f, core + 6f, core + 6f);
                    }
                }
                using (SolidBrush b = new SolidBrush(Dot)) { g.FillEllipse(b, cx - core / 2f, cy - core / 2f, core, core); }
                x += (int)core + 10;
            }

            Color ink = ForeColor;
            if (!Enabled) { ink = Color.FromArgb(120, ForeColor); }
            else if (hover && !HoverInk.IsEmpty) { ink = HoverInk; }

            TextFormatFlags flags = TextFormatFlags.NoPrefix | TextFormatFlags.SingleLine |
                                    TextFormatFlags.VerticalCenter | TextFormatFlags.EndEllipsis;
            bool left = TextAlign == ContentAlignment.MiddleLeft || TextAlign == ContentAlignment.TopLeft ||
                        TextAlign == ContentAlignment.BottomLeft;
            flags |= left ? TextFormatFlags.Left : TextFormatFlags.HorizontalCenter;
            Rectangle text = new Rectangle(x, 0, Width - x - Padding.Right, Height);
            TextRenderer.DrawText(g, Text, Font, text, ink, flags);
        }
    }
}
'@
}

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
# The BOGOS mark. Cut from the rules app's favicon by tools/make_widget_icon.ps1,
# which records where it comes from and how the checkerboard is removed.
$iconFile = Join-Path $PSScriptRoot 'bogos-icon.png'

# Glyphs built from code points rather than typed into the file: Windows
# PowerShell reads a .ps1 as ANSI unless it carries a byte-order mark, and these
# would come back mangled.
$dash = [string][char]0x2013
$cross = [string][char]0x00D7
# The system icon font's minimise and close glyphs.
$glyphMinimise = [string][char]0xE921
$glyphClose = [string][char]0xE8BB
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

# Segoe UI Variable is the Windows 11 text face; Segoe UI is its Windows 10
# predecessor. The window glyphs come from the system icon font, Fluent on 11
# and MDL2 on 10, so minimise and close look like every other window's.
$uiFace = 'Segoe UI'
$glyphFace = ''
try {
    if ($families -contains 'Segoe UI Variable Text') { $uiFace = 'Segoe UI Variable Text' }
    if ($families -contains 'Segoe Fluent Icons') { $glyphFace = 'Segoe Fluent Icons' }
    elseif ($families -contains 'Segoe MDL2 Assets') { $glyphFace = 'Segoe MDL2 Assets' }
} catch { }

function New-UiFont($size) {
    return New-Object System.Drawing.Font($uiFace, $size)
}

function New-GlyphFont($size) {
    if ($glyphFace) { return New-Object System.Drawing.Font($glyphFace, $size) }
    return New-UiFont ($size + 2)
}

# --- the mark ----------------------------------------------------------------
# One source image, scaled to whatever a face of the widget needs. Read once and
# held: a Bitmap owns an open file handle, and the tray would reopen it on every
# state change.

function Get-Mark {
    if (-not $script:markSource) {
        $script:markSource = [System.Drawing.Bitmap]::FromFile($iconFile)
    }
    return $script:markSource
}

function New-MarkBitmap($size) {
    $src = Get-Mark
    $bmp = New-Object System.Drawing.Bitmap($size, $size)
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
    $g.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
    $g.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
    $g.Clear([System.Drawing.Color]::Transparent)
    $g.DrawImage($src, 0, 0, $size, $size)
    $g.Dispose()
    return $bmp
}

function New-MarkIcon($size) {
    # GetHicon returns a copy, so the bitmap can go; the icon is assigned once
    # per face and lives as long as the panel. Icon.FromHandle does not own the
    # handle, and one per face is not a leak worth a P/Invoke to destroy.
    $bmp = New-MarkBitmap $size
    $handle = $bmp.GetHicon()
    $bmp.Dispose()
    return [System.Drawing.Icon]::FromHandle($handle)
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
# START is the one lit key on the case: amber, the colour of "the bot works".
$amberHover = [System.Drawing.Color]::FromArgb(242, 176, 82)
$amberDown = [System.Drawing.Color]::FromArgb(210, 143, 48)
$amberInk = [System.Drawing.Color]::FromArgb(33, 24, 12)
$recFill = [System.Drawing.Color]::FromArgb(48, 30, 27)
$recEdge = [System.Drawing.Color]::FromArgb(110, 52, 44)

# --- the grid ---------------------------------------------------------------
# 4px units. Wide and short like a rack unit: it reads as gear and covers less
# of whatever is behind it than a square card would. Rows are 20px: at 15 the
# 8pt text had its top shaved off.
$W = 360
$PAD = 14
$RAIL_H = 38
$WELL_Y = 46
$WELL_H = 62
$KEY_Y = 120
$KEY_W = 216
$KEY_H = 36
$FOOT_Y = 168
$ROW_H = 20
$DEST_Y = $FOOT_Y + 8
$API_Y = $DEST_Y + $ROW_H
$LOG_Y = $API_Y + $ROW_H
$H = $LOG_Y + $ROW_H + 12
# The label column of the footer rows.
$TAG_W = 52

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

function Get-Route {
    # Which transcriber the bot will use. Empty means "read it off the model id",
    # which is how an .env written before the second route existed still works,
    # so the badge shows what that resolves to and not what the file says.
    $named = (Get-EnvValue 'STT_CHUNK_PROVIDER').Trim().ToLower()
    if ($named) { return $named }
    if ((Get-EnvValue 'STT_CHUNK_MODEL') -match 'gemini') { return 'gemini' }
    return 'voxtral'
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
    # A lit lamp glows, so the control is larger than the dot; ($x, $y) is
    # still where the dot's own box starts.
    $lamp = New-Object BogosUi.Lamp
    $lamp.Core = $diameter
    $lamp.Location = New-Object System.Drawing.Point(($x - 5), ($y - 5))
    $lamp.Size = New-Object System.Drawing.Size(($diameter + 10), ($diameter + 10))
    $lamp.Fill = $fill
    return $lamp
}

function Set-Lamp($lamp, $fill, [bool]$glow = $false) {
    $lamp.Set($fill, $glow)
}

function New-TrayIcon($fill, $ring) {
    # The tray is the panel's second face: minimising must not cost the one bit
    # that matters, which is whether anything is being captured. So the mark is
    # the icon, and the lamp is a dot in the corner of it the mark leaves empty.
    # 32px, not 16: the shell asks for whatever size its scaling needs, and a
    # mark this detailed survives a downscale better than an upscale.
    $size = 32
    $bmp = New-MarkBitmap $size
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
    # Top left: the one corner of the mark the crown and the flame leave empty,
    # so the lamp sits beside the mark instead of on its face.
    $dot = 9
    $x = 1
    $y = 1
    # A collar in the case colour cuts the dot out of whatever is behind it; on
    # a light taskbar the mark's own edge is what would otherwise swallow it.
    $collar = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(230, $case.R, $case.G, $case.B))
    $g.FillEllipse($collar, ($x - 1), ($y - 1), ($dot + 2), ($dot + 2))
    $collar.Dispose()
    if ($fill.A -gt 0) {
        $brush = New-Object System.Drawing.SolidBrush($fill)
        $g.FillEllipse($brush, $x, $y, $dot, $dot)
        $brush.Dispose()
    }
    $pen = New-Object System.Drawing.Pen($ring, 1.5)
    $g.DrawEllipse($pen, $x, $y, $dot, $dot)
    $pen.Dispose()
    $g.Dispose()
    $handle = $bmp.GetHicon()
    $bmp.Dispose()
    return [System.Drawing.Icon]::FromHandle($handle)
}

# An icon is judged by eye. -PreviewDir renders the three tray marks and the
# panel mark, on the light and dark strips the shell shows them on, and exits
# without opening the panel.
if ($PreviewDir) {
    if (-not (Test-Path $PreviewDir)) {
        New-Item -ItemType Directory -Path $PreviewDir -Force | Out-Null
    }
    $states = [ordered]@{
        busy    = (New-TrayIcon $amber $case)
        idle    = (New-TrayIcon $screenInk $case)
        stopped = (New-TrayIcon ([System.Drawing.Color]::Transparent) $screenInk)
    }
    $sheet = New-Object System.Drawing.Bitmap(560, 370)
    $g = [System.Drawing.Graphics]::FromImage($sheet)
    $g.Clear([System.Drawing.Color]::FromArgb(243, 243, 243))
    $g.FillRectangle((New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(32, 32, 32))), 0, 180, 560, 190)
    $g.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
    $g.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
    $y = 12
    foreach ($name in $states.Keys) {
        $img = $states[$name].ToBitmap()
        foreach ($strip in @(0, 180)) {
            $x = 18
            foreach ($s in @(16, 20, 24, 32, 48)) {
                $g.DrawImage($img, $x, ($y + $strip + 20), $s, $s)
                $x += $s + 14
            }
        }
        $img.Dispose()
        $y += 50
    }
    $panelMark = New-MarkBitmap 48
    $g.DrawImage($panelMark, 430, 24, 48, 48)
    $panelMark.Dispose()
    $g.Dispose()
    $out = Join-Path $PreviewDir 'tray-marks.png'
    $sheet.Save($out, [System.Drawing.Imaging.ImageFormat]::Png)
    $sheet.Dispose()
    Write-Output $out
    exit 0
}

# --- the panel --------------------------------------------------------------
$form = New-Object BogosUi.CaseForm
$form.EdgeColor = $hair
$form.FormBorderStyle = 'None'
$form.StartPosition = 'Manual'
$form.Location = Load-Position
$form.Size = New-Object System.Drawing.Size($W, $H)
# Not topmost: the panel should fall behind other windows like anything else.
$form.TopMost = $false
$form.ShowInTaskbar = $false
$form.BackColor = $case
$form.Text = 'BOGOS bot'
$form.Icon = New-MarkIcon 32

# The mark, then the wordmark: the panel says what it is before it says what it
# is doing.
$mark = New-Object System.Windows.Forms.PictureBox
$mark.Location = New-Object System.Drawing.Point($PAD, 8)
$mark.Size = New-Object System.Drawing.Size(22, 22)
$mark.SizeMode = 'Zoom'
$mark.BackColor = $case
$mark.Image = New-MarkBitmap 44
$form.Controls.Add($mark)

$identity = New-Object System.Windows.Forms.Label
$identity.Text = 'BOGOS'
$identity.Font = New-DisplayFont 12
$identity.ForeColor = $ivory
$identity.BackColor = $case
$identity.Location = New-Object System.Drawing.Point(($PAD + 28), 9)
$identity.AutoSize = $true
$form.Controls.Add($identity)

# The route badge: two transcribers, one word, a click to swap them. A chip in
# the title bar, because it says what the machine is, not what it is doing.
$routeBadge = New-Object BogosUi.PillButton
$routeBadge.Text = ''
$routeBadge.Font = New-UiFont 7.5
$routeBadge.ForeColor = $amber
$routeBadge.HoverInk = $ivoryBright
$routeBadge.Fill = $case
$routeBadge.FillHover = $keyFace
$routeBadge.Border = $hair
$routeBadge.FocusBorder = $screenInk
$routeBadge.Radius = 10
$routeBadge.Location = New-Object System.Drawing.Point(($W - $PAD - 60 - 74), 9)
$routeBadge.Size = New-Object System.Drawing.Size(70, 21)
$routeBadge.AccessibleName = 'Cloud transcription route'
$routeBadge.TabIndex = 5
$form.Controls.Add($routeBadge)

function New-WindowKey($glyph, $x) {
    $key = New-Object BogosUi.PillButton
    $key.Text = $glyph
    $key.Font = New-GlyphFont 8
    $key.ForeColor = $screenInk
    $key.HoverInk = $ivory
    $key.FillHover = $keyFace
    $key.Radius = 6
    $key.Location = New-Object System.Drawing.Point($x, 8)
    $key.Size = New-Object System.Drawing.Size(26, 24)
    $key.TabStop = $false
    return $key
}

$minimise = New-WindowKey $(if ($glyphFace) { $glyphMinimise } else { $dash }) ($W - $PAD - 56)
$minimise.AccessibleName = 'Minimise to the tray'
$form.Controls.Add($minimise)

$close = New-WindowKey $(if ($glyphFace) { $glyphClose } else { $cross }) ($W - $PAD - 26)
$close.AccessibleName = 'Close the panel'
$form.Controls.Add($close)

# The seam between what this thing is and what it is doing.
$seam = New-Object System.Windows.Forms.Panel
$seam.Location = New-Object System.Drawing.Point(0, $RAIL_H)
$seam.Size = New-Object System.Drawing.Size($W, 1)
$seam.BackColor = $hair
$form.Controls.Add($seam)

# The readout is recessed, not raised. A well says "a window into the machine";
# a raised card says "a panel from a dashboard", which is the thing to avoid.
$wellPanel = New-Object BogosUi.Well
$wellPanel.Location = New-Object System.Drawing.Point($PAD, $WELL_Y)
$wellPanel.Size = New-Object System.Drawing.Size(($W - 2 * $PAD), $WELL_H)
$wellPanel.BackColor = $well
$wellPanel.Edge = $hair
$wellPanel.Radius = 9
$form.Controls.Add($wellPanel)

$stateLamp = New-Lamp 14 18 9 $lampOff
$wellPanel.Controls.Add($stateLamp)

$stateWord = New-Object System.Windows.Forms.Label
$stateWord.Text = 'STARTING'
$stateWord.Font = New-DisplayFont 15
$stateWord.ForeColor = $ivory
$stateWord.BackColor = $well
$stateWord.Location = New-Object System.Drawing.Point(34, 9)
$stateWord.AutoSize = $true
$wellPanel.Controls.Add($stateWord)

$subLine = New-Object System.Windows.Forms.Label
$subLine.Text = ''
$subLine.Font = New-UiFont 8.5
$subLine.ForeColor = $screenInk
$subLine.BackColor = $well
$subLine.Location = New-Object System.Drawing.Point(35, 36)
$subLine.AutoSize = $true
$wellPanel.Controls.Add($subLine)

$uptime = New-Object System.Windows.Forms.Label
$uptime.Text = ''
$uptime.Font = New-DisplayFont 11
$uptime.ForeColor = $screenInk
$uptime.BackColor = $well
$uptime.Location = New-Object System.Drawing.Point(($wellPanel.Width - 124), 14)
$uptime.Size = New-Object System.Drawing.Size(110, 20)
$uptime.TextAlign = 'MiddleRight'
$wellPanel.Controls.Add($uptime)

# The key. Lit amber when it would start the bot, a quiet case key when it would
# stop it: the one action that matters right now is the one that stands out.
$keyBtn = New-Object BogosUi.PillButton
$keyBtn.Text = 'START'
$keyBtn.Font = New-DisplayFont 11
$keyBtn.Size = New-Object System.Drawing.Size($KEY_W, $KEY_H)
$keyBtn.Location = New-Object System.Drawing.Point($PAD, $KEY_Y)
$keyBtn.Radius = 9
$keyBtn.FocusBorder = $ivoryDim
$keyBtn.TabIndex = 0
$form.Controls.Add($keyBtn)

function Set-KeyFace($lit) {
    if ($lit) {
        $keyBtn.Fill = $amber
        $keyBtn.FillHover = $amberHover
        $keyBtn.FillDown = $amberDown
        $keyBtn.Border = $amber
        $keyBtn.ForeColor = $amberInk
        $keyBtn.HoverInk = $amberInk
    } else {
        $keyBtn.Fill = $keyFace
        $keyBtn.FillHover = $keyFaceHover
        $keyBtn.FillDown = $keyFaceDown
        $keyBtn.Border = $hair
        $keyBtn.ForeColor = $ivory
        $keyBtn.HoverInk = $ivoryBright
    }
    $keyBtn.Invalidate()
}

# The latch: a lamp and the word REC on one rounded switch, the way a recorder
# labels its own. The word names the function, the lamp answers whether it is
# on, and the footer says it in words too, so colour is never the only signal.
$recWord = New-Object BogosUi.PillButton
$recWord.Text = 'REC'
$recWord.Font = New-DisplayFont 10
$recWord.Size = New-Object System.Drawing.Size(($W - 2 * $PAD - $KEY_W - 10), $KEY_H)
$recWord.Location = New-Object System.Drawing.Point(($PAD + $KEY_W + 10), $KEY_Y)
$recWord.Radius = 9
$recWord.Padding = New-Object System.Windows.Forms.Padding(14, 0, 8, 0)
$recWord.TextAlign = 'MiddleLeft'
$recWord.FocusBorder = $ivoryDim
$recWord.HoverInk = $ivoryBright
$recWord.TabIndex = 1
$form.Controls.Add($recWord)

# A second, fainter seam: above it the controls, below it the settings.
$footSeam = New-Object System.Windows.Forms.Panel
$footSeam.Location = New-Object System.Drawing.Point($PAD, $FOOT_Y)
$footSeam.Size = New-Object System.Drawing.Size(($W - 2 * $PAD), 1)
$footSeam.BackColor = $hair
$form.Controls.Add($footSeam)

# Footer rows: a small label, then the value. A value that does something when
# clicked is a button that looks like text until the pointer is on it.
function New-FootTag($text, $y) {
    $tag = New-Object System.Windows.Forms.Label
    $tag.Text = $text
    $tag.Font = New-UiFont 7
    $tag.ForeColor = $screenInk
    $tag.BackColor = $case
    $tag.Location = New-Object System.Drawing.Point($PAD, $y)
    $tag.Size = New-Object System.Drawing.Size($TAG_W, $ROW_H)
    $tag.TextAlign = 'MiddleLeft'
    $form.Controls.Add($tag)
    return $tag
}

function New-FootValue($y, $tabIndex) {
    $row = New-Object BogosUi.PillButton
    $row.Font = New-UiFont 8.5
    $row.ForeColor = $ivoryDim
    $row.HoverInk = $ivory
    $row.FillHover = $keyFace
    $row.FocusBorder = $hair
    $row.Radius = 5
    $row.Padding = New-Object System.Windows.Forms.Padding(6, 0, 6, 0)
    $row.TextAlign = 'MiddleLeft'
    $row.Location = New-Object System.Drawing.Point(($PAD + $TAG_W - 6), $y)
    $row.Size = New-Object System.Drawing.Size(($W - 2 * $PAD - $TAG_W + 6), $ROW_H)
    $row.TabIndex = $tabIndex
    $form.Controls.Add($row)
    return $row
}

$destTag = New-FootTag 'SAVE TO' $DEST_Y
$dest = New-FootValue $DEST_Y 2
$dest.Text = 'choose a folder'

$apiTag = New-FootTag 'KEY' $API_Y
$apiRow = New-FootValue $API_Y 3
$apiRow.Text = 'add an OpenRouter key'

$logTag = New-FootTag 'LOG' $LOG_Y
$logLine = New-Object System.Windows.Forms.Label
$logLine.Text = ''
$logLine.Font = New-UiFont 8
$logLine.ForeColor = $screenInk
$logLine.BackColor = $case
$logLine.Location = New-Object System.Drawing.Point(($PAD + $TAG_W), $LOG_Y)
$logLine.Size = New-Object System.Drawing.Size(($W - 2 * $PAD - $TAG_W), $ROW_H)
$logLine.TextAlign = 'MiddleLeft'
$logLine.AutoEllipsis = $true
$form.Controls.Add($logLine)

# ToolTip is its own component in WinForms; controls have no ToolTipText at
# runtime, only the extender adds one at design time.
$tipProvider = New-Object System.Windows.Forms.ToolTip

# --- the key dialog ---------------------------------------------------------
# A modal of the same case, not a system dialog: it is the one place a secret
# is typed, and it should look like part of the thing it configures.

function New-DialogButton($text, $x, $y, $w) {
    $button = New-Object BogosUi.PillButton
    $button.Text = $text
    $button.Font = New-DisplayFont 10
    $button.Size = New-Object System.Drawing.Size($w, 34)
    $button.Location = New-Object System.Drawing.Point($x, $y)
    $button.Radius = 8
    $button.Fill = $keyFace
    $button.FillHover = $keyFaceHover
    $button.FillDown = $keyFaceDown
    $button.Border = $hair
    $button.FocusBorder = $ivoryDim
    $button.ForeColor = $ivory
    $button.HoverInk = $ivoryBright
    return $button
}

function Show-KeyDialog {
    $dlgW = 440
    $dlgH = 250

    $dlg = New-Object BogosUi.CaseForm
    $dlg.EdgeColor = $hair
    $dlg.FormBorderStyle = 'None'
    $dlg.StartPosition = 'CenterParent'
    $dlg.Size = New-Object System.Drawing.Size($dlgW, $dlgH)
    $dlg.BackColor = $case
    $dlg.ShowInTaskbar = $false
    $dlg.Icon = $form.Icon
    $dlg.KeyPreview = $true

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
    $save.Fill = $amber
    $save.FillHover = $amberHover
    $save.FillDown = $amberDown
    $save.Border = $amber
    $save.ForeColor = $amberInk
    $save.HoverInk = $amberInk
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
        if ($keyBtn.Text -ne 'START' -or $keyBtn.Fill -ne $amber) { $keyBtn.Text = 'START'; Set-KeyFace $true }
        $trayKey = 'stopped'
    } else {
        Set-Lamp $stateLamp $(if ($bot.busy) { $amber } else { $lampOff }) $bot.busy
        $stateWord.Text = $bot.word
        $stateWord.ForeColor = $ivory
        $subLine.Text = $bot.sub
        $uptime.Text = 'up ' + (Format-Uptime $proc.CreationDate)
        if ($keyBtn.Text -ne 'STOP' -or $keyBtn.Fill -ne $keyFace) { $keyBtn.Text = 'STOP'; Set-KeyFace $false }
        $trayKey = if ($bot.busy) { 'busy' } else { 'idle' }
    }

    $logLine.Text = Get-LastLogLine $proc

    $recording = Get-Recording
    if ($recording.enabled) {
        $recWord.Dot = $recRed
        $recWord.DotGlow = $true
        $recWord.Fill = $recFill
        $recWord.FillHover = $recFill
        $recWord.Border = $recEdge
        $recWord.ForeColor = $recRed
        $recWord.Text = 'REC ON'
        $dest.Text = Shorten-Path $recording.folder 48
    } else {
        $recWord.Dot = $recOff
        $recWord.DotGlow = $false
        $recWord.Fill = $case
        $recWord.FillHover = $keyFace
        $recWord.Border = $hair
        $recWord.ForeColor = $screenInk
        $recWord.Text = 'REC OFF'
        if ($recording.folder) { $dest.Text = 'not saving to disk' } else { $dest.Text = 'choose a folder' }
    }
    $recWord.Invalidate()

    if (Get-EnvValue 'OPENROUTER_API_KEY') {
        $apiRow.Text = 'OpenRouter key set'
    } else {
        $apiRow.Text = 'add an OpenRouter key'
    }

    $route = Get-Route
    $routeBadge.Text = $route.ToUpper()
    # Amber is the route this team settled on; the other one is dimmed, not
    # hidden, because it is one click away.
    $routeBadge.ForeColor = $(if ($route -eq 'gemini') { $ivoryDim } else { $amber })

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
    $tipProvider.SetToolTip($dest, $tipRecording + ' Click to choose the folder.')
    $tipProvider.SetToolTip($keyBtn, 'Start or stop the bot process.')
    $tipProvider.SetToolTip($apiRow, 'The key cloud transcription uses. Saved in .env on this machine.')
    $routeBadge.AccessibleDescription = $route
    $tipProvider.SetToolTip($routeBadge,
        'Cloud transcription uses ' + $route + ' on the next start. Click to switch route; ' +
        'a bot that is already running keeps the one it started with.')
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

$keyBtn.Add_Click({
    $keyBtn.Enabled = $false
    if (Get-BotProcess) { Stop-Bot } else { Start-Bot }
    Start-Sleep -Milliseconds 700
    $keyBtn.Enabled = $true
    Update-Panel
})

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

$dest.Add_Click({
    $folder = Choose-Folder
    if (-not $folder) { return }
    $recording = Get-Recording
    Set-Recording $recording.enabled $folder
    Update-Panel
})

$apiRow.Add_Click({ Show-KeyDialog })
$routeBadge.Add_Click({
    $next = $(if ((Get-Route) -eq 'gemini') { 'voxtral' } else { 'gemini' })
    Set-EnvValue 'STT_CHUNK_PROVIDER' $next
    Update-Panel
})

$close.Add_Click({ Save-Position; $form.Close() })
$minimise.Add_Click({ $form.Hide() })
$form.Add_FormClosing({ Save-Position })

$drag = $false
$grab = New-Object System.Drawing.Point(0, 0)
$startDrag = { param($sender, $e) $script:drag = $true; $script:grab = $e.Location }
$doDrag = { param($sender, $e) if ($script:drag) { $form.Location = New-Object System.Drawing.Point(($form.Location.X - $script:grab.X + $e.X), ($form.Location.Y - $script:grab.Y + $e.Y)) } }
$endDrag = { param($sender, $e) $script:drag = $false }
foreach ($c in @($form, $mark, $identity, $seam, $wellPanel, $stateLamp, $stateWord, $subLine, $uptime, $footSeam, $destTag, $apiTag, $logTag, $logLine)) {
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
