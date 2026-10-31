<#
    从 Logo.png 生成应用图标（PNG → ICO / PNG）。

        .\packaging\make-icon.ps1

    产物：
      packaging\icon.ico     PyInstaller 的 exe 图标：16/24/32/48/64/128 用 BMP 帧，256 用 PNG 帧
      ..\static\favicon.ico  浏览器标签页：16/32/48
      ..\static\icon.png     256×256，侧栏品牌图与 apple-touch-icon

    只用 System.Drawing，不依赖 Pillow：开发环境里 Pillow 虽在 dev 组声明，但 uv.lock 未收录，
    `uv run` 会因此重新解析依赖；图标生成不该被这件事卡住。
#>
param(
    [string]$Source = (Join-Path $PSScriptRoot "Logo.png")
)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Drawing

if (-not (Test-Path -LiteralPath $Source)) {
    throw "找不到源图：$Source"
}

$root = Split-Path -Parent $PSScriptRoot

function New-ScaledBitmap([System.Drawing.Image]$image, [int]$size) {
    $bmp = New-Object System.Drawing.Bitmap($size, $size, [System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $g.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
    $g.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
    $g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::HighQuality
    # 源图 767×768 不是正方形，直接铺满整格（差 1px，肉眼不可见），避免留透明缝
    $g.DrawImage($image, 0, 0, $size, $size)
    $g.Dispose()
    return $bmp
}

# 32bpp BMP 帧：BITMAPINFOHEADER + 自下而上的 BGRA + 全 0 的 AND 掩码
function Get-DibFrame([System.Drawing.Bitmap]$bmp) {
    $size = $bmp.Width
    $rect = New-Object System.Drawing.Rectangle(0, 0, $size, $size)
    $bits = $bmp.LockBits(
        $rect,
        [System.Drawing.Imaging.ImageLockMode]::ReadOnly,
        [System.Drawing.Imaging.PixelFormat]::Format32bppArgb
    )
    $stride = $bits.Stride
    $pixels = New-Object byte[] ($stride * $size)
    [System.Runtime.InteropServices.Marshal]::Copy($bits.Scan0, $pixels, 0, $pixels.Length)
    $bmp.UnlockBits($bits)

    $ms = New-Object System.IO.MemoryStream
    $bw = New-Object System.IO.BinaryWriter($ms)
    # biHeight 写两倍：XOR 图 + AND 掩码
    $bw.Write([UInt32]40)
    $bw.Write([Int32]$size)
    $bw.Write([Int32]($size * 2))
    $bw.Write([UInt16]1)
    $bw.Write([UInt16]32)
    $bw.Write([UInt32]0)
    $bw.Write([UInt32]($size * $size * 4))
    $bw.Write([Int32]0)
    $bw.Write([Int32]0)
    $bw.Write([UInt32]0)
    $bw.Write([UInt32]0)
    for ($row = $size - 1; $row -ge 0; $row--) {
        $bw.Write($pixels, $row * $stride, $size * 4)
    }
    $maskRowBytes = [int]([Math]::Ceiling($size / 32.0) * 4)
    $bw.Write((New-Object byte[] ($maskRowBytes * $size)))
    $bw.Flush()
    $bytes = $ms.ToArray()
    $bw.Dispose()
    $ms.Dispose()
    return , $bytes
}

function Get-PngFrame([System.Drawing.Bitmap]$bmp) {
    $ms = New-Object System.IO.MemoryStream
    $bmp.Save($ms, [System.Drawing.Imaging.ImageFormat]::Png)
    $bytes = $ms.ToArray()
    $ms.Dispose()
    return , $bytes
}

function Write-Ico([string]$path, [object[]]$frames) {
    $ms = New-Object System.IO.MemoryStream
    $bw = New-Object System.IO.BinaryWriter($ms)
    $bw.Write([UInt16]0)
    $bw.Write([UInt16]1)
    $bw.Write([UInt16]$frames.Count)
    $offset = 6 + 16 * $frames.Count
    foreach ($frame in $frames) {
        # 256 及以上在目录里写 0
        $dim = 0
        if ($frame.Size -lt 256) { $dim = $frame.Size }
        $bw.Write([Byte]$dim)
        $bw.Write([Byte]$dim)
        $bw.Write([Byte]0)
        $bw.Write([Byte]0)
        $bw.Write([UInt16]1)
        $bw.Write([UInt16]32)
        $bw.Write([UInt32]$frame.Bytes.Length)
        $bw.Write([UInt32]$offset)
        $offset += $frame.Bytes.Length
    }
    foreach ($frame in $frames) {
        $bw.Write($frame.Bytes)
    }
    $bw.Flush()
    [System.IO.File]::WriteAllBytes($path, $ms.ToArray())
    $bw.Dispose()
    $ms.Dispose()
    Write-Host ("写出 {0}（{1} 帧，{2:N0} KB）" -f $path, $frames.Count, ((Get-Item $path).Length / 1KB))
}

function New-Frames([System.Drawing.Image]$image, [int[]]$sizes) {
    $frames = @()
    foreach ($size in $sizes) {
        $bmp = New-ScaledBitmap $image $size
        $bytes = Get-DibFrame $bmp
        $bmp.Dispose()
        $frames += [pscustomobject]@{ Size = $size; Bytes = $bytes }
    }
    return , $frames
}

$image = [System.Drawing.Image]::FromFile((Resolve-Path -LiteralPath $Source).Path)
try {
    Write-Host ("源图 {0}：{1}×{2}" -f (Split-Path -Leaf $Source), $image.Width, $image.Height)

    # exe 图标：小尺寸用 BMP 帧（资源管理器与任务栏最稳），256 用 PNG 帧压体积
    $exeFrames = New-Frames $image @(16, 24, 32, 48, 64, 128)
    $large = New-ScaledBitmap $image 256
    $exeFrames += [pscustomobject]@{ Size = 256; Bytes = (Get-PngFrame $large) }
    Write-Ico (Join-Path $PSScriptRoot "icon.ico") $exeFrames

    Write-Ico (Join-Path $root "static\favicon.ico") (New-Frames $image @(16, 32, 48))

    $large.Save((Join-Path $root "static\icon.png"), [System.Drawing.Imaging.ImageFormat]::Png)
    Write-Host ("写出 {0}（256×256）" -f (Join-Path $root "static\icon.png"))
    $large.Dispose()
} finally {
    $image.Dispose()
}
