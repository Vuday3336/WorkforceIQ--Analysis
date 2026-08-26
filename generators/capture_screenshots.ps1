<#
.SYNOPSIS
    Captures the Power BI Desktop window into docs/dashboard_screenshots/.

.DESCRIPTION
    Captures ONLY the Power BI Desktop window, not the whole desktop, so
    nothing else on screen ends up in the image.

    Power BI Desktop has no command line and no automation surface, so page
    navigation cannot be scripted -- you click the page tab, the script grabs
    the window. Run it once per page.

.EXAMPLE
    # open the report, select the page you want, then:
    powershell -ExecutionPolicy Bypass -File generators/capture_screenshots.ps1 -Name 01_executive

.EXAMPLE
    # capture all four with a countdown between, switching pages yourself
    powershell -ExecutionPolicy Bypass -File generators/capture_screenshots.ps1 -All
#>
param(
    [string]$Name,
    [switch]$All,
    [int]$DelaySeconds = 6
)

Add-Type -AssemblyName System.Drawing
Add-Type -AssemblyName System.Windows.Forms

Add-Type @"
using System;
using System.Runtime.InteropServices;
public class Win {
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int c);
    [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
    [StructLayout(LayoutKind.Sequential)]
    public struct RECT { public int Left, Top, Right, Bottom; }
}
"@

$OutDir = Join-Path $PSScriptRoot "..\docs\dashboard_screenshots"
if (-not (Test-Path $OutDir)) { New-Item -ItemType Directory -Path $OutDir | Out-Null }

function Get-PowerBiWindow {
    $p = Get-Process | Where-Object {
        $_.ProcessName -eq "PBIDesktop" -and $_.MainWindowHandle -ne 0
    } | Select-Object -First 1
    if (-not $p) {
        Write-Host "Power BI Desktop is not running (looking for process PBIDesktop)." -ForegroundColor Red
        Write-Host "Open powerbi\WorkforceIQ.pbip first, then run this again."
        exit 1
    }
    return $p
}

function Capture([string]$file) {
    $proc = Get-PowerBiWindow
    $h = $proc.MainWindowHandle

    [Win]::ShowWindow($h, 3) | Out-Null   # 3 = maximize
    [Win]::SetForegroundWindow($h) | Out-Null
    Start-Sleep -Milliseconds 1200        # let the canvas finish repainting

    $r = New-Object Win+RECT
    [Win]::GetWindowRect($h, [ref]$r) | Out-Null
    $w = $r.Right - $r.Left
    $ht = $r.Bottom - $r.Top
    if ($w -le 0 -or $ht -le 0) {
        Write-Host "Could not read the window bounds." -ForegroundColor Red
        exit 1
    }

    $bmp = New-Object System.Drawing.Bitmap $w, $ht
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $g.CopyFromScreen($r.Left, $r.Top, 0, 0, $bmp.Size)

    $path = Join-Path $OutDir "$file.png"
    $bmp.Save($path, [System.Drawing.Imaging.ImageFormat]::Png)
    $g.Dispose(); $bmp.Dispose()

    $kb = [math]::Round((Get-Item $path).Length / 1KB)
    Write-Host "  saved $file.png  ($w x $ht, $kb KB)" -ForegroundColor Green
}

if ($All) {
    $pages = @(
        @{ File = "01_executive";    Label = "Executive Overview" },
        @{ File = "02_tenure";       Label = "Tenure & Cohort Analysis" },
        @{ File = "03_compensation"; Label = "Compensation & Satisfaction" },
        @{ File = "04_watchlist";    Label = "Attrition Risk Watchlist" }
    )
    Write-Host ""
    Write-Host "Capturing 4 pages. Click each page tab when prompted." -ForegroundColor Cyan
    Write-Host ""
    foreach ($p in $pages) {
        Write-Host "NEXT: click the '$($p.Label)' tab in Power BI." -ForegroundColor Yellow
        for ($i = $DelaySeconds; $i -gt 0; $i--) {
            Write-Host "`r  capturing in $i ... " -NoNewline
            Start-Sleep -Seconds 1
        }
        Write-Host "`r                        " -NoNewline
        Write-Host "`r" -NoNewline
        Capture $p.File
    }
    Write-Host ""
    Write-Host "Done. Commit with:" -ForegroundColor Cyan
    Write-Host "  git add docs/dashboard_screenshots && git commit -m 'docs: dashboard screenshots' && git push"
}
elseif ($Name) {
    Capture $Name
}
else {
    Write-Host "Usage:"
    Write-Host "  -Name 01_executive   capture the current page under that name"
    Write-Host "  -All                 walk all four pages with a countdown between"
}
