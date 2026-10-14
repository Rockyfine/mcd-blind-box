<#
.SYNOPSIS
    把「麦门盲盒」安装成本机的 WorkBuddy Skill。

.DESCRIPTION
    做的事只有一件：把仓库里的 skill/mcd-blind-box-challenge/ 复制到
    ~/.workbuddy/skills/ 下。宿主会自动扫描该目录里的 SKILL.md，无需注册。

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts/install_skill.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts/install_skill.ps1 -Force
#>

[CmdletBinding()]
param(
    [string]$TargetRoot = (Join-Path $HOME '.workbuddy/skills'),
    [switch]$Force
)

$ErrorActionPreference = 'Stop'

$SkillName = 'mcd-blind-box-challenge'
$RepoRoot  = Split-Path -Parent $PSScriptRoot
$Source    = Join-Path $RepoRoot "skill/$SkillName"
$Dest      = Join-Path $TargetRoot $SkillName

if (-not (Test-Path (Join-Path $Source 'SKILL.md'))) {
    Write-Error "找不到 $Source\SKILL.md —— 请在仓库内运行本脚本（解析到的仓库根：$RepoRoot）"
    exit 2
}

if (Test-Path $Dest) {
    if (-not $Force) {
        Write-Host "已存在：$Dest"
        Write-Host '如果确认要覆盖，请加 -Force 再跑一次。'
        exit 3
    }
    Write-Host "覆盖已有 Skill：$Dest"
    Remove-Item -Recurse -Force $Dest
}

New-Item -ItemType Directory -Force -Path $TargetRoot | Out-Null
New-Item -ItemType Directory -Force -Path $Dest | Out-Null
Copy-Item -Recurse -Force (Join-Path $Source '*') $Dest

Write-Host "已安装到：$Dest"
Write-Host ''
Write-Host '目录内容：'
Get-ChildItem -Recurse -File $Dest |
    ForEach-Object { '  ' + $_.FullName.Substring($Dest.Length + 1) }
Write-Host ''
Write-Host '下一步：重启对话（或新开一个对话），然后用自然语言触发，例如'
Write-Host '  「帮我开个 30 块以内的盲盒」'
Write-Host '  「来个不超过 600 大卡的，要辣的」'
