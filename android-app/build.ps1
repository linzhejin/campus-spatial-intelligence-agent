# 珞珈智行 APK 手工构建链：aapt2 → javac → d8 → aapt add → zipalign → apksigner
# 无 Gradle / 无第三方依赖，签名密钥保存在本目录（勿提交）
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$proj = $root
$tools = Join-Path $root "..\tools"
$bt    = Join-Path $tools "android-14"                    # build-tools 34
$platJar = Join-Path $tools "android-35\android.jar"      # platform 35
$jdkBin  = Join-Path $tools "jdk-17.0.20.1+1\bin"
$env:JAVA_HOME = Join-Path $tools "jdk-17.0.20.1+1"
$env:PATH = "$env:JAVA_HOME\bin;$env:PATH"

$verCode = 4
$verName = "1.3.0"
$build = Join-Path $proj "build"
Remove-Item -Recurse -Force $build -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path "$build\classes", "$build\dex" | Out-Null

Write-Host "== 1. aapt2 compile =="
& (Join-Path $bt "aapt2.exe") compile --dir (Join-Path $proj "res") -o "$build\res.zip"
if ($LASTEXITCODE -ne 0) { throw "aapt2 compile failed" }

Write-Host "== 2. aapt2 link =="
& (Join-Path $bt "aapt2.exe") link -o "$build\unsigned.apk" `
    -I $platJar `
    --manifest (Join-Path $proj "AndroidManifest.xml") `
    -R "$build\res.zip" --auto-add-overlay `
    --min-sdk-version 26 --target-sdk-version 34 `
    --version-code $verCode --version-name $verName
if ($LASTEXITCODE -ne 0) { throw "aapt2 link failed" }

Write-Host "== 3. javac =="
& (Join-Path $jdkBin "javac.exe") --release 8 -encoding UTF-8 `
    -classpath $platJar `
    -d "$build\classes" `
    (Get-ChildItem (Join-Path $proj "src") -Recurse -Filter *.java | ForEach-Object { $_.FullName })
if ($LASTEXITCODE -ne 0) { throw "javac failed" }

Write-Host "== 4. d8 (dex) =="
& (Join-Path $bt "d8.bat") --lib $platJar --release --min-api 26 `
    --output "$build\dex" `
    (Get-ChildItem "$build\classes" -Recurse -Filter *.class | ForEach-Object { $_.FullName })
if ($LASTEXITCODE -ne 0) { throw "d8 failed" }

Write-Host "== 5. aapt add classes.dex =="
Push-Location "$build\dex"
& (Join-Path $bt "aapt.exe") add "$build\unsigned.apk" "classes.dex"
Pop-Location
if ($LASTEXITCODE -ne 0) { throw "aapt add failed" }

Write-Host "== 6. zipalign =="
& (Join-Path $bt "zipalign.exe") -f 4 "$build\unsigned.apk" "$build\aligned.apk"
if ($LASTEXITCODE -ne 0) { throw "zipalign failed" }

Write-Host "== 7. keytool (首次生成签名密钥) =="
$ks = Join-Path $proj "whu-walker.keystore"
if (-not (Test-Path $ks)) {
    $pwFile = Join-Path $proj "keystore-password.txt"
    if (Test-Path $pwFile) { $ksPass = (Get-Content $pwFile -Raw).Trim() }
    else {
        $ksPass = "Ww-" + [Guid]::NewGuid().ToString("N").Substring(0, 20)
        Set-Content -Path $pwFile -Value $ksPass -NoNewline
        Write-Host "已生成新签名密钥，密码写入 keystore-password.txt（请勿提交/请备份）"
    }
    & (Join-Path $jdkBin "keytool.exe") -genkeypair -v `
        -keystore $ks -alias whuwalker -keyalg RSA -keysize 2048 -validity 10950 `
        -storepass $ksPass -keypass $ksPass `
        -dname "CN=WHU Walker, OU=Engineering, O=WHU-Walker, L=Wuhan, ST=Hubei, C=CN"
    if ($LASTEXITCODE -ne 0) { throw "keytool failed" }
} else {
    $ksPass = (Get-Content (Join-Path $proj "keystore-password.txt") -Raw).Trim()
}

Write-Host "== 8. apksigner sign =="
& (Join-Path $bt "apksigner.bat") sign `
    --ks $ks --ks-pass "pass:$ksPass" --key-pass "pass:$ksPass" `
    --out "$build\whu-walker.apk" "$build\aligned.apk"
if ($LASTEXITCODE -ne 0) { throw "apksigner failed" }

Write-Host "== 9. verify =="
& (Join-Path $bt "apksigner.bat") verify --print-certs "$build\whu-walker.apk"
if ($LASTEXITCODE -ne 0) { throw "apksigner verify failed" }

Write-Host "DONE -> $build\whu-walker.apk"
