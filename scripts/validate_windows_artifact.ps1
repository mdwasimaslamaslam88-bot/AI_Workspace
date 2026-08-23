param(
  [Parameter(Mandatory = $true)]
  [string]$OutputDirectory
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$releaseRoot = Join-Path $repositoryRoot "apps/desktop/src-tauri/target/release"
$binary = Join-Path $releaseRoot "work-station-desktop.exe"
$installers = @(
  Get-ChildItem -Path (Join-Path $releaseRoot "bundle/nsis") -Filter "*.exe" -File
)
if ($installers.Count -ne 1) {
  throw "Expected exactly one Windows NSIS installer."
}
$installer = $installers[0].FullName

function Assert-PortableExecutable([string]$Path) {
  $bytes = [System.IO.File]::ReadAllBytes($Path)
  if ($bytes.Length -lt 256 -or $bytes[0] -ne 0x4d -or $bytes[1] -ne 0x5a) {
    throw "Artifact is not an MZ executable: $([System.IO.Path]::GetFileName($Path))"
  }
  $peOffset = [System.BitConverter]::ToInt32($bytes, 0x3c)
  if (
    $peOffset -lt 0 -or
    $peOffset + 4 -gt $bytes.Length -or
    $bytes[$peOffset] -ne 0x50 -or
    $bytes[$peOffset + 1] -ne 0x45 -or
    $bytes[$peOffset + 2] -ne 0x00 -or
    $bytes[$peOffset + 3] -ne 0x00
  ) {
    throw "Artifact does not contain a valid PE signature: $([System.IO.Path]::GetFileName($Path))"
  }
}

Assert-PortableExecutable $binary
Assert-PortableExecutable $installer

$versionInfo = (Get-Item $binary).VersionInfo
if ($versionInfo.ProductName -ne "WORK STATION") {
  throw "Windows executable product metadata is incorrect."
}

& 7z t $installer | Out-Null
if ($LASTEXITCODE -ne 0) {
  throw "NSIS package integrity validation failed."
}

$scanRoot = Join-Path $env:RUNNER_TEMP ("work-station-windows-scan-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $scanRoot | Out-Null
& 7z x "-o$scanRoot" $installer | Out-Null
if ($LASTEXITCODE -ne 0) {
  throw "NSIS package extraction failed."
}

function Assert-NoPattern([string]$Pattern, [string[]]$Paths, [string]$Failure) {
  foreach ($path in $Paths) {
    $files = if ((Get-Item $path).PSIsContainer) {
      Get-ChildItem -Path $path -File -Recurse -Force
    } else {
      @(Get-Item $path)
    }
    foreach ($file in $files) {
      $bytes = [System.IO.File]::ReadAllBytes($file.FullName)
      $content = [System.Text.Encoding]::Latin1.GetString($bytes)
      if ([System.Text.RegularExpressions.Regex]::IsMatch($content, $Pattern)) {
        throw $Failure
      }
    }
  }
}

$scanPaths = @($binary, $installer, $scanRoot)
Assert-NoPattern `
  'USER_PROVISIONING_TOKEN_DIGEST|X-User-Provisioning-Token|\.ai_workspace_provisioning_token|-----BEGIN .*PRIVATE KEY-----' `
  $scanPaths `
  "Windows artifacts contain operator configuration or private key material."
Assert-NoPattern `
  '(?i)C:\\Users\\|D:\\a\\|/home/|/tmp/' `
  $scanPaths `
  "Windows artifacts contain a build-machine filesystem path."

$process = Start-Process -FilePath $binary -PassThru
try {
  Start-Sleep -Seconds 12
  $process.Refresh()
  if ($process.HasExited) {
    throw "The production Windows executable exited during launch smoke."
  }
} finally {
  if (-not $process.HasExited) {
    Stop-Process -Id $process.Id
    $process.WaitForExit(10000) | Out-Null
  }
}

$resolvedOutput = if ([System.IO.Path]::IsPathRooted($OutputDirectory)) {
  [System.IO.Path]::GetFullPath($OutputDirectory)
} else {
  [System.IO.Path]::GetFullPath((Join-Path $repositoryRoot $OutputDirectory))
}
if (Test-Path $resolvedOutput) {
  throw "Refusing to overwrite an existing Windows artifact output directory."
}
New-Item -ItemType Directory -Path $resolvedOutput | Out-Null
$stableInstaller = Join-Path $resolvedOutput "Work_Station_Windows_Setup.exe"
$stableBinary = Join-Path $resolvedOutput "Work_Station_Windows.exe"
Copy-Item $installer $stableInstaller
Copy-Item $binary $stableBinary

$checksumLines = foreach ($artifact in @($stableInstaller, $stableBinary)) {
  $hash = (Get-FileHash -Algorithm SHA256 $artifact).Hash.ToLowerInvariant()
  "$hash  $([System.IO.Path]::GetFileName($artifact))"
}
$checksumLines | Set-Content -Path (Join-Path $resolvedOutput "Work_Station_Windows.sha256") -Encoding utf8NoBOM

Write-Host "windows validation: PE metadata, NSIS integrity, secret/path scan, launch smoke, and checksums passed"
