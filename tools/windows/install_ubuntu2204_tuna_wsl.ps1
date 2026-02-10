param(
  [string]$DistroName = "Ubuntu2204TUNA",
  [string]$InstallDir = "",
  [string]$DownloadDir = "",
  [string]$UserName = "",
  [switch]$SkipAptMirror
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

function Require-Cmd([string]$name) {
  if (-not (Get-Command $name -ErrorAction SilentlyContinue)) {
    throw "Missing required command: $name"
  }
}

Require-Cmd "wsl.exe"

if ([string]::IsNullOrWhiteSpace($InstallDir)) {
  $InstallDir = Join-Path $env:USERPROFILE ("wsl\\{0}" -f $DistroName)
}
if ([string]::IsNullOrWhiteSpace($DownloadDir)) {
  $DownloadDir = Join-Path $env:USERPROFILE "Downloads"
}
if ([string]::IsNullOrWhiteSpace($UserName)) {
  $UserName = $env:USERNAME
}

New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
New-Item -ItemType Directory -Force -Path $DownloadDir | Out-Null

$tarName = "ubuntu-jammy-wsl-amd64-ubuntu22.04lts.rootfs.tar.gz"
$tarPath = Join-Path $DownloadDir $tarName
$shaPath = Join-Path $DownloadDir "SHA256SUMS"

$uTar = "https://mirrors.tuna.tsinghua.edu.cn/ubuntu-cloud-images/wsl/jammy/current/$tarName"
$uSha = "https://mirrors.tuna.tsinghua.edu.cn/ubuntu-cloud-images/wsl/jammy/current/SHA256SUMS"

Write-Host "[wsl] DistroName:  $DistroName"
Write-Host "[wsl] InstallDir:  $InstallDir"
Write-Host "[wsl] DownloadDir: $DownloadDir"
Write-Host "[wsl] UserName:    $UserName"

Write-Host "[wsl] Downloading SHA256SUMS..."
Invoke-WebRequest -Uri $uSha -OutFile $shaPath -UseBasicParsing

Write-Host "[wsl] Downloading rootfs tarball..."
Invoke-WebRequest -Uri $uTar -OutFile $tarPath -UseBasicParsing

Write-Host "[wsl] Verifying SHA256..."
$sumLine = (Select-String -Path $shaPath -Pattern [regex]::Escape($tarName) | Select-Object -First 1).Line
if (-not $sumLine) { throw "SHA256SUMS did not contain: $tarName" }
$expected = ($sumLine -split "\\s+")[0].ToLower()
$actual = (Get-FileHash -Algorithm SHA256 $tarPath).Hash.ToLower()
if ($actual -ne $expected) { throw "SHA256 mismatch: expected $expected got $actual" }
Write-Host ("[wsl] OK: SHA256 matches ({0})" -f $actual)

Write-Host "[wsl] Importing distro..."
& wsl.exe --import $DistroName $InstallDir $tarPath
if ($LASTEXITCODE -ne 0) { throw "wsl --import failed (exit $LASTEXITCODE)" }

Write-Host "[wsl] Initializing user + packages (root)..."

$initScript = @"
set -euo pipefail
user="$UserName"

apt-get update -y
DEBIAN_FRONTEND=noninteractive apt-get install -y sudo ca-certificates curl git build-essential python3 python3-pip python3-venv pkg-config

if ! id -u "$UserName" >/dev/null 2>&1; then
  useradd -m -s /bin/bash "$UserName"
  usermod -aG sudo "$UserName"
fi

echo "$UserName ALL=(ALL) NOPASSWD:ALL" >"/etc/sudoers.d/99-${UserName}-nopasswd"
chmod 0440 "/etc/sudoers.d/99-${UserName}-nopasswd"

printf "[user]\ndefault=%s\n" "$UserName" >/etc/wsl.conf

if [ "$SkipAptMirror" = "0" ]; then
  cp /etc/apt/sources.list "/etc/apt/sources.list.bak.$(date +%Y%m%d)"
  sed -i 's|http://archive.ubuntu.com/ubuntu/|https://mirrors.tuna.tsinghua.edu.cn/ubuntu/|g; s|http://security.ubuntu.com/ubuntu|https://mirrors.tuna.tsinghua.edu.cn/ubuntu/|g' /etc/apt/sources.list
  apt-get update -y
fi
"@

# Avoid CRLF issues by writing via UTF8 and trimming CRs in bash.
$tmp = Join-Path $env:TEMP "wsl_init_$DistroName.sh"
[System.IO.File]::WriteAllText($tmp, $initScript, (New-Object System.Text.UTF8Encoding($false)))
& wsl.exe -d $DistroName -u root -- bash -lc ("tr -d '\r' < /mnt/c/" + ($tmp -replace "^[A-Za-z]:\\\\","" -replace "\\\\","/") + " | bash -s")
if ($LASTEXITCODE -ne 0) { throw "init script failed (exit $LASTEXITCODE)" }

Write-Host "[wsl] Restarting distro so /etc/wsl.conf takes effect..."
& wsl.exe --terminate $DistroName | Out-Null

Write-Host "[wsl] Done. List distros:"
& wsl.exe --list --verbose

