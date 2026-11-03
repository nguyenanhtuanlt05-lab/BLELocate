<#
  Opens Windows Firewall on P1 for BLE Locate (inbound TCP dashboard port and
  UDP discovery port), reports block rules that would override it, and warns
  when the Wi-Fi is set to "Public". Anchors (P2/P3) need no rules.

  Options:  -HttpPort 8780 -DiscoveryPort 8781   ports, if P1 was started with others
            -AnySource      allow devices outside P1's own subnet (default: local subnet only)
            -AllowPublic    also allow on networks marked Public (not recommended)
            -Remove         delete the BLE Locate rules
#>
param(
    [int]$HttpPort = 8780,
    [int]$DiscoveryPort = 8781,
    [switch]$AnySource,
    [switch]$AllowPublic,
    [switch]$Remove
)

$ErrorActionPreference = "Stop"

$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "Windows will ask for administrator permission to change the firewall..."
    $argList = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$PSCommandPath`"",
                 "-HttpPort", $HttpPort, "-DiscoveryPort", $DiscoveryPort)
    if ($AnySource) { $argList += "-AnySource" }
    if ($AllowPublic) { $argList += "-AllowPublic" }
    if ($Remove) { $argList += "-Remove" }
    try {
        Start-Process -FilePath "powershell.exe" -Verb RunAs -ArgumentList $argList -Wait
    } catch {
        Write-Host "Administrator permission was not granted, so nothing was changed." -ForegroundColor Yellow
        Write-Host "Next step: run firewall_setup.bat again and choose Yes, or ask the computer's administrator."
        Read-Host "Press Enter to close"
    }
    exit
}

$existing = @(Get-NetFirewallRule -DisplayName "BLE Locate*" -ErrorAction SilentlyContinue)
if ($existing.Count -gt 0) { $existing | Remove-NetFirewallRule }

if ($Remove) {
    Write-Host "Removed $($existing.Count) BLE Locate firewall rule(s)." -ForegroundColor Green
    Read-Host "Press Enter to close"
    exit 0
}

$profiles = "Domain,Private"
if ($AllowPublic) { $profiles = "Domain,Private,Public" }
$remote = "LocalSubnet"
if ($AnySource) { $remote = "Any" }

New-NetFirewallRule -DisplayName "BLE Locate dashboard (TCP $HttpPort)" -Direction Inbound -Action Allow `
    -Protocol TCP -LocalPort $HttpPort -Profile $profiles -RemoteAddress $remote | Out-Null
New-NetFirewallRule -DisplayName "BLE Locate discovery (UDP $DiscoveryPort)" -Direction Inbound -Action Allow `
    -Protocol UDP -LocalPort $DiscoveryPort -Profile $profiles -RemoteAddress $remote | Out-Null
Write-Host "Allowed inbound TCP $HttpPort and UDP $DiscoveryPort on $profiles networks from $remote." -ForegroundColor Green

# Clicking "Cancel" on Windows' own firewall prompt creates Block rules for the
# program, and Block always wins over Allow.
$blocks = @(Get-NetFirewallApplicationFilter |
    Where-Object { $_.Program -match '(python|pythonw|BLELocate)\.exe$' } |
    Get-NetFirewallRule |
    Where-Object { $_.Direction -eq "Inbound" -and $_.Action -eq "Block" -and $_.Enabled -eq "True" })
if ($blocks.Count -gt 0) {
    Write-Host ""
    Write-Host "These inbound BLOCK rules can stop P1 from being reached:" -ForegroundColor Yellow
    foreach ($rule in $blocks) {
        $program = ($rule | Get-NetFirewallApplicationFilter).Program
        Write-Host "  - $($rule.DisplayName)  [$($rule.Profile)]  $program"
    }
    $answer = Read-Host "Remove them? (Y/N)"
    if ($answer -match '^[Yy]') {
        $blocks | Remove-NetFirewallRule
        Write-Host "Removed." -ForegroundColor Green
    }
}

$public = @(Get-NetConnectionProfile | Where-Object { $_.NetworkCategory -eq "Public" })
foreach ($net in $public) {
    Write-Host ""
    Write-Host "The network '$($net.Name)' ($($net.InterfaceAlias)) is set to Public." -ForegroundColor Yellow
    if (-not $AllowPublic) {
        Write-Host "  The rules above do not apply there. If this is your own or a trusted network, make it Private:"
        Write-Host "  Settings > Network & internet > Wi-Fi (or Ethernet) > this network > Network profile type: Private"
        Write-Host "  or run in an administrator PowerShell:"
        Write-Host "    Set-NetConnectionProfile -InterfaceIndex $($net.InterfaceIndex) -NetworkCategory Private"
    }
}

Write-Host ""
Read-Host "Done. Press Enter to close"
