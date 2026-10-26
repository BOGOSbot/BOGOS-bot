# Cut the BOGOS mark out of the rules app's favicon.
#
# The favicon in the MHQ-parser repository is a screenshot of the mark over the
# transparency checkerboard an image editor draws, and the checkerboard is baked
# into the pixels: two near-neutral greys, 48-73, behind a skull whose outline
# is near black and whose fill is white, orange and red. So the background is
# not a known colour to key out, it is the region reachable from the edge whose
# pixels are neutral and mid-dark. Black is excluded by the floor, which keeps
# the outline, the socket and the eye that sit inside the mark.
#
#   .\tools\make_widget_icon.ps1
#   .\tools\make_widget_icon.ps1 -Source C:\path\favicon.PNG -Size 512
#
# Writes widget/bogos-icon.png: a square RGBA PNG with transparent corners, the
# mark filling the frame with a 4% margin.

param(
    [string]$Source = 'C:\GitRepos\mhq-parser\img\favicon.PNG',
    [string]$Output = '',
    [int]$Size = 256
)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing
if (-not $Output) { $Output = Join-Path (Split-Path -Parent $PSScriptRoot) 'widget\bogos-icon.png' }

Add-Type -ReferencedAssemblies 'System.Drawing' -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Drawing.Imaging;
using System.Runtime.InteropServices;

public static class BogosIconPrep
{
    // The mark as RGBA, cropped to its own bounds, everything reachable from
    // the outside set to transparent.
    static Bitmap CutBackground(string path)
    {
        using (Bitmap file = new Bitmap(path))
        {
            // The favicon is a screenshot, and it carries the editor's 2px white
            // frame just inside its own edge. Left in, that frame walls off the
            // flood fill from the checkerboard it encloses. Four pixels also
            // clear the rounded corner's antialiasing.
            Rectangle inset = new Rectangle(4, 4, file.Width - 8, file.Height - 8);
            using (Bitmap src = file.Clone(inset, PixelFormat.Format32bppArgb))
            using (Bitmap bmp = new Bitmap(src.Width, src.Height, PixelFormat.Format32bppArgb))
            {
                using (Graphics g = Graphics.FromImage(bmp)) { g.DrawImageUnscaled(src, 0, 0); }

                int w = bmp.Width, h = bmp.Height;
                BitmapData data = bmp.LockBits(new Rectangle(0, 0, w, h),
                    ImageLockMode.ReadWrite, PixelFormat.Format32bppArgb);
                int stride = data.Stride;
                byte[] px = new byte[stride * h];
                Marshal.Copy(data.Scan0, px, 0, px.Length);

                bool[] visited = new bool[w * h];
                bool[] isBackground = new bool[w * h];
                Queue<int> edge = new Queue<int>();

                // Seed every border pixel. Nothing is decided here: the loop
                // decides, so a border pixel that is part of the mark stays.
                for (int x = 0; x < w; x++) { edge.Enqueue(x); edge.Enqueue((h - 1) * w + x); }
                for (int y = 0; y < h; y++) { edge.Enqueue(y * w); edge.Enqueue(y * w + w - 1); }

                while (edge.Count > 0)
                {
                    int i = edge.Dequeue();
                    if (visited[i]) { continue; }
                    visited[i] = true;

                    int o = (i / w) * stride + (i % w) * 4;
                    int b = px[o], gc = px[o + 1], r = px[o + 2];
                    int mx = Math.Max(r, Math.Max(gc, b));
                    int mn = Math.Min(r, Math.Min(gc, b));
                    int avg = (r + gc + b) / 3;
                    bool neutral = (mx - mn) <= 12;
                    // 35 is above the black outline and below the checkerboard;
                    // 95 is above both checker greys and below every mark colour.
                    if (!(neutral && avg >= 35 && avg <= 95)) { continue; }

                    isBackground[i] = true;
                    int x0 = i % w, y0 = i / w;
                    if (x0 > 0) { edge.Enqueue(i - 1); }
                    if (x0 < w - 1) { edge.Enqueue(i + 1); }
                    if (y0 > 0) { edge.Enqueue(i - w); }
                    if (y0 < h - 1) { edge.Enqueue(i + w); }
                }

                int left = w, top = h, right = -1, bottom = -1;
                for (int y = 0; y < h; y++)
                {
                    for (int x = 0; x < w; x++)
                    {
                        int i = y * w + x;
                        int o = y * stride + x * 4;
                        if (isBackground[i]) { px[o + 3] = 0; continue; }
                        if (x < left) { left = x; }
                        if (x > right) { right = x; }
                        if (y < top) { top = y; }
                        if (y > bottom) { bottom = y; }
                    }
                }
                if (right < left || bottom < top) { throw new InvalidOperationException("no mark found in " + path); }

                Marshal.Copy(px, 0, data.Scan0, px.Length);
                bmp.UnlockBits(data);

                Rectangle bounds = new Rectangle(left, top, right - left + 1, bottom - top + 1);
                return bmp.Clone(bounds, PixelFormat.Format32bppArgb);
            }
        }
    }

    public static void Build(string sourcePath, string outputPath, int size)
    {
        using (Bitmap mark = CutBackground(sourcePath))
        {
            // Square canvas with a 4% margin: in the tray and the title bar the
            // mark must not touch its own edge, or the skull looks clipped.
            int side = (int)Math.Round(Math.Max(mark.Width, mark.Height) * 1.08);
            using (Bitmap square = new Bitmap(side, side, PixelFormat.Format32bppArgb))
            using (Graphics g = Graphics.FromImage(square))
            {
                g.Clear(Color.Transparent);
                g.InterpolationMode = InterpolationMode.HighQualityBicubic;
                g.PixelOffsetMode = PixelOffsetMode.HighQuality;
                g.SmoothingMode = SmoothingMode.AntiAlias;
                g.DrawImage(mark, (side - mark.Width) / 2, (side - mark.Height) / 2, mark.Width, mark.Height);
                using (Bitmap final = new Bitmap(size, size, PixelFormat.Format32bppArgb))
                using (Graphics fg = Graphics.FromImage(final))
                {
                    fg.Clear(Color.Transparent);
                    fg.InterpolationMode = InterpolationMode.HighQualityBicubic;
                    fg.PixelOffsetMode = PixelOffsetMode.HighQuality;
                    fg.DrawImage(square, 0, 0, size, size);
                    final.Save(outputPath, ImageFormat.Png);
                }
            }
        }
    }
}
'@

try {
    [BogosIconPrep]::Build($Source, $Output, $Size)
} catch {
    # The widget keeps its mark open for as long as it runs, and GDI+ refuses
    # to overwrite a file another process holds.
    throw ("Could not write {0}. Close the widget first: it keeps the current mark open while it runs." -f $Output)
}
$fi = Get-Item $Output
$check = [System.Drawing.Image]::FromFile($fi.FullName)
"wrote {0} ({1} x {2}, {3} bytes)" -f $fi.FullName, $check.Width, $check.Height, $fi.Length
$check.Dispose()
