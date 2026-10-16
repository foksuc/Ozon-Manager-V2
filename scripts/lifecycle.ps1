param([Parameter(Mandatory=$true)][ValidateSet('start','stop')][string]$Action)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
# Process-local built-in modules only; never inherit a user-supplied PSModulePath.
$env:PSModulePath = Join-Path $PSHOME 'Modules'
[Console]::OutputEncoding = New-Object Text.UTF8Encoding($false)
$OutputEncoding = [Console]::OutputEncoding
$ProjectRoot = [IO.Path]::GetFullPath((Split-Path $PSScriptRoot -Parent))
Write-Host '[Ozon Manager] Checking project paths and dependency locks...'
function File-Sha256([string]$Target) {
    $Stream = [IO.File]::OpenRead($Target)
    $Hasher = [Security.Cryptography.SHA256]::Create()
    try { return [BitConverter]::ToString($Hasher.ComputeHash($Stream)).Replace('-','').ToLowerInvariant() }
    finally { $Stream.Dispose(); $Hasher.Dispose() }
}
function Assert-Confined([string]$Target) {
    $Resolved = [IO.Path]::GetFullPath($Target)
    if (($Resolved -ne $ProjectRoot) -and (-not $Resolved.StartsWith($ProjectRoot + '\', [StringComparison]::OrdinalIgnoreCase))) { throw 'External path denied' }
    $Cursor = $Resolved
    while ($Cursor) {
        try { $Attributes = [IO.File]::GetAttributes($Cursor) }
        catch [IO.FileNotFoundException] { $Attributes = 0 }
        catch [IO.DirectoryNotFoundException] { $Attributes = 0 }
        if ($Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Reparse point denied' }
        $Cursor = [IO.Path]::GetDirectoryName($Cursor)
    }
    return $Resolved
}
# Complete preflight happens before the first download, write or subprocess.
Assert-Confined $ProjectRoot | Out-Null
foreach ($Name in @('frontend','backend','.cache','data','scripts')) {
    $Target = Assert-Confined (Join-Path $ProjectRoot $Name)
    if ([IO.Directory]::Exists($Target)) {
        # Native enumeration avoids a PowerShell pipeline for every dependency file.
        # Check each directory before descending, so junctions are never followed.
        $Directories = New-Object 'Collections.Generic.Stack[IO.DirectoryInfo]'
        $Directories.Push((New-Object IO.DirectoryInfo($Target)))
        while ($Directories.Count) {
            foreach ($Entry in $Directories.Pop().GetFileSystemInfos()) {
                if ($Entry.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Nested reparse point denied' }
                if ($Entry.Attributes -band [IO.FileAttributes]::Directory) { $Directories.Push($Entry) }
            }
        }
    }
}
foreach ($Config in @('.npmrc','frontend\.npmrc')) { if (Test-Path -LiteralPath (Join-Path $ProjectRoot $Config)) { throw 'Local npm override denied' } }
foreach ($Lock in @('backend\requirements.lock.txt','frontend\package-lock.json')) {
    if (-not (Test-Path -PathType Leaf -LiteralPath (Assert-Confined (Join-Path $ProjectRoot $Lock)))) { throw 'Missing dependency lock' }
}
foreach ($Requirement in [IO.File]::ReadAllLines((Join-Path $ProjectRoot 'backend\requirements.lock.txt'))) {
    if ($Requirement.Trim() -and $Requirement -notmatch '^[A-Za-z0-9_.-]+==[A-Za-z0-9_.+-]+$') { throw 'Unpinned Python dependency' }
}
# Windows PowerShell5 ConvertFrom-Json cannot represent the npm lock's empty root key.
Add-Type -AssemblyName System.Web.Extensions
$LockParser = New-Object Web.Script.Serialization.JavaScriptSerializer
$LockParser.MaxJsonLength = 8000000
$NodeLock = $LockParser.DeserializeObject([IO.File]::ReadAllText((Join-Path $ProjectRoot 'frontend\package-lock.json')))
if ($NodeLock['lockfileVersion'] -ne 3 -or -not $NodeLock.ContainsKey('packages')) { throw 'Unsupported npm lock' }
foreach ($Property in $NodeLock['packages'].GetEnumerator()) {
    if (-not $Property.Key) { continue }
    $Package = $Property.Value
    if ($Property.Key -notmatch '^node_modules/' -or $Property.Key -match '(^|/)\.\.(/|$)' -or $Package.ContainsKey('link')) { throw 'Linked/external dependency denied' }
    if ($Package['resolved'] -notmatch '^https://registry\.npmjs\.org/' -or $Package['integrity'] -notmatch '^sha512-[A-Za-z0-9+/]+={0,2}$') { throw 'Missing npm integrity/trusted registry' }
}
if ($env:PROCESSOR_ARCHITECTURE -ne 'AMD64' -and $env:PROCESSOR_ARCHITEW6432 -ne 'AMD64') { throw 'Windows x64 required' }
$RuntimeRoot = Assert-Confined (Join-Path $ProjectRoot '.cache\runtimes')
$PythonExe = Join-Path $RuntimeRoot 'python\python.exe'
if ($Action -eq 'stop' -and -not (Test-Path -LiteralPath $PythonExe)) {
    # Existing development checkout: no download or installation is needed to stop.
    $PythonExe = Join-Path $ProjectRoot 'backend\.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $PythonExe)) { Write-Host 'Application is not installed/running.'; exit 0 }
}
if ($Action -eq 'start') {
    $BootstrapHasher = [Security.Cryptography.SHA256]::Create()
    try { $BootstrapName = 'Local\OzonManagerBootstrap-' + [BitConverter]::ToString($BootstrapHasher.ComputeHash([Text.Encoding]::UTF8.GetBytes($ProjectRoot.ToLowerInvariant()))).Replace('-','') }
    finally { $BootstrapHasher.Dispose() }
    $BootstrapMutex = New-Object Threading.Mutex($false, $BootstrapName)
    if (-not $BootstrapMutex.WaitOne(30000)) { $BootstrapMutex.Dispose(); throw 'Another bootstrap is still running' }
    try {
    New-Item -ItemType Directory -Force -Path $RuntimeRoot | Out-Null
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $Runtimes = @(
        @{ Name='python'; Url='https://www.python.org/ftp/python/3.14.8/python-3.14.8-amd64.zip'; Sha='4873947a8afc037846b180312b83c744a4146a851cfd316a75c3125a4d8299da'; Strip='' },
        @{ Name='node'; Url='https://nodejs.org/dist/v24.9.0/node-v24.9.0-win-x64.zip'; Sha='6873514c3e6a012917cc6f95ce48a6289253370d025f1b69db290d70feebfa6e'; Strip='node-v24.9.0-win-x64/' }
    )
    foreach ($Runtime in $Runtimes) {
        $Archive = Assert-Confined (Join-Path $RuntimeRoot ($Runtime.Name + '.zip'))
        if (-not (Test-Path -LiteralPath $Archive)) {
            Write-Host ('[Ozon Manager] Downloading official ' + $Runtime.Name + ' runtime. Please wait...')
            $Partial = Assert-Confined ($Archive + '.part')
            [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
            Invoke-WebRequest -UseBasicParsing -MaximumRedirection 0 -TimeoutSec 120 -Uri $Runtime.Url -OutFile $Partial
            if ((File-Sha256 $Partial) -ne $Runtime.Sha) { throw 'Runtime SHA256 mismatch' }
            Move-Item -LiteralPath $Partial -Destination $Archive
        }
        Write-Host ('[Ozon Manager] Verifying SHA256 for ' + $Runtime.Name + '...')
        if ((File-Sha256 $Archive) -ne $Runtime.Sha) { throw 'Cached runtime SHA256 mismatch' }
        Write-Host ('[Ozon Manager] Preparing local ' + $Runtime.Name + ' runtime...')
        $Destination = Assert-Confined (Join-Path $RuntimeRoot $Runtime.Name)
        $Zip = [IO.Compression.ZipFile]::OpenRead($Archive)
        try {
            foreach ($Entry in $Zip.Entries) {
                $Relative = $Entry.FullName
                if ($Runtime.Strip) {
                    if (-not $Relative.StartsWith($Runtime.Strip, [StringComparison]::Ordinal)) { throw 'Unexpected archive root' }
                    $Relative = $Relative.Substring($Runtime.Strip.Length)
                }
                if (-not $Relative) { continue }
                if ($Relative -match '(^[\/]|:|(^|[\/])\.\.([\/]|$))' -or (($Entry.ExternalAttributes -shr 16) -band 0xF000) -eq 0xA000) { throw 'Unsafe archive entry' }
                $Output = Assert-Confined ([IO.Path]::Combine($Destination, $Relative))
                if ($Relative.EndsWith('/')) { [IO.Directory]::CreateDirectory($Output) | Out-Null; continue }
                [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($Output)) | Out-Null
                # Verify existing bytes against the pinned archive, including running exes.
                $Matches = $false
                if ([IO.File]::Exists($Output)) {
                    $EntryStream = $Entry.Open()
                    $Hasher = [Security.Cryptography.SHA256]::Create()
                    try { $EntryHash = [BitConverter]::ToString($Hasher.ComputeHash($EntryStream)).Replace('-','') }
                    finally { $EntryStream.Dispose(); $Hasher.Dispose() }
                    $Matches = (File-Sha256 $Output) -eq $EntryHash.ToLowerInvariant()
                }
                if (-not $Matches) { [IO.Compression.ZipFileExtensions]::ExtractToFile($Entry, $Output, $true) }
            }
        } finally { $Zip.Dispose() }
        Write-Host ('[Ozon Manager] ' + $Runtime.Name + ' runtime is ready.')
    }
    } finally { $BootstrapMutex.ReleaseMutex(); $BootstrapMutex.Dispose() }
}
# Child receives a fresh allowlisted environment, never inherited Python/npm overrides.
Write-Host ('[Ozon Manager] Running ' + $Action + ' with isolated project settings...')
$StartInfo = New-Object Diagnostics.ProcessStartInfo
$StartInfo.FileName = $PythonExe
$StartInfo.Arguments = '-I -B -X utf8 "' + (Join-Path $ProjectRoot 'scripts\lifecycle.py') + '" ' + $Action
$StartInfo.WorkingDirectory = $ProjectRoot
$StartInfo.UseShellExecute = $false
$SavedSystemRoot = $env:SystemRoot
$StartInfo.EnvironmentVariables.Clear()
$StartInfo.EnvironmentVariables['SystemRoot'] = $SavedSystemRoot
$StartInfo.EnvironmentVariables['WINDIR'] = $SavedSystemRoot
$StartInfo.EnvironmentVariables['COMSPEC'] = Join-Path $SavedSystemRoot 'System32\cmd.exe'
$StartInfo.EnvironmentVariables['PATH'] = Join-Path $SavedSystemRoot 'System32'
$StartInfo.EnvironmentVariables['TEMP'] = Join-Path $ProjectRoot '.cache\tmp'
$StartInfo.EnvironmentVariables['TMP'] = Join-Path $ProjectRoot '.cache\tmp'
$StartInfo.EnvironmentVariables['USERPROFILE'] = Join-Path $ProjectRoot '.cache\profile'
$StartInfo.EnvironmentVariables['APPDATA'] = Join-Path $ProjectRoot '.cache\profile\AppData\Roaming'
$StartInfo.EnvironmentVariables['LOCALAPPDATA'] = Join-Path $ProjectRoot '.cache\profile\AppData\Local'
$StartInfo.EnvironmentVariables['PYTHONUTF8'] = '1'
$StartInfo.EnvironmentVariables['PYTHONDONTWRITEBYTECODE'] = '1'
$Child = [Diagnostics.Process]::Start($StartInfo)
$Child.WaitForExit()
if ($Child.ExitCode -eq 0 -and $Action -eq 'start') {
    # The normal parent owns this user-facing action. Never hand URL handling to
    # the isolated package/runtime child or its synthetic USERPROFILE.
    Write-Host '[Ozon Manager] Opening http://127.0.0.1:8000 in your default browser...'
    $BrowserInfo = New-Object Diagnostics.ProcessStartInfo
    $BrowserInfo.FileName = 'http://127.0.0.1:8000/'
    $BrowserInfo.UseShellExecute = $true
    $BrowserInfo.Verb = 'open'
    $BrowserInfo.WindowStyle = [Diagnostics.ProcessWindowStyle]::Hidden
    try { [Diagnostics.Process]::Start($BrowserInfo) | Out-Null }
    catch { Write-Warning 'The website is running. Open http://127.0.0.1:8000/ manually; the default browser handler failed.' }
}
exit $Child.ExitCode
