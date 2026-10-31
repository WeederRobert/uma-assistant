<#
.SYNOPSIS
把 uma-assistant/.github 下的 instructions 与 skills 同步到工作区根目录的 .github。

DESCRIPTION
VS Code 只扫描「工作区根」的 .github，所以项目内的那份必须镜像一份出去；
项目内的 instructions 与 skills 是源，根目录的是产物。

.PARAMETER Check
只比对不写；有不一致时退出码 1。
#>
[CmdletBinding()]
param([switch]$Check)

$ErrorActionPreference = "Stop"
$source = Join-Path $PSScriptRoot "..\.github"
$target = Join-Path $PSScriptRoot "..\..\.github"
$utf8 = New-Object System.Text.UTF8Encoding($false)

$pairs = New-Object System.Collections.ArrayList

$instructions = Join-Path $source "instructions\uma-assistant.instructions.md"
if (Test-Path $instructions) {
  [void]$pairs.Add([pscustomobject]@{
    From = $instructions
    To   = Join-Path $target "instructions\uma-assistant.instructions.md"
  })
}

$skillsDir = Join-Path $source "skills"
if (Test-Path $skillsDir) {
  foreach ($dir in Get-ChildItem $skillsDir -Directory) {
    $file = Join-Path $dir.FullName "SKILL.md"
    if (Test-Path $file) {
      [void]$pairs.Add([pscustomobject]@{
        From = $file
        To   = Join-Path $target ("skills\{0}\SKILL.md" -f $dir.Name)
      })
    }
  }
}

if ($pairs.Count -eq 0) {
  Write-Host "no source files under $source"
  exit 1
}

$dirty = 0
foreach ($pair in $pairs) {
  $content = [System.IO.File]::ReadAllText($pair.From)
  $current = $null
  if (Test-Path $pair.To) { $current = [System.IO.File]::ReadAllText($pair.To) }
  if ($current -eq $content) {
    Write-Host ("same    {0}" -f $pair.To)
    continue
  }
  $dirty++
  if ($Check) {
    Write-Host ("differs {0}" -f $pair.To)
    continue
  }
  $parent = Split-Path -Parent $pair.To
  if (-not (Test-Path $parent)) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
  [System.IO.File]::WriteAllText($pair.To, $content, $utf8)
  Write-Host ("wrote   {0}" -f $pair.To)
}

if ($Check -and $dirty -gt 0) {
  Write-Host ("out of sync: {0} of {1} files" -f $dirty, $pairs.Count)
  exit 1
}

Write-Host ("done: {0} files, {1} updated" -f $pairs.Count, $dirty)
