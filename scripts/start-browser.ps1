# Opens the product-scout Chrome profile with the DevTools port, for the app in Docker (docker compose --profile app).
# Chrome listens only on this computer (127.0.0.1). Log in to amazon.se in this window yourself; the app never
# types passwords. Close all other windows of this profile first: Chrome locks a profile that is in use.
$root = Split-Path -Parent $PSScriptRoot
$profile = Join-Path $root "browser-profile"
$chrome = "C:\Program Files\Google\Chrome\Application\chrome.exe"
Start-Process -FilePath $chrome -ArgumentList @("--user-data-dir=`"$profile`"", "--remote-debugging-port=9222",
    "--no-first-run", "--lang=sv-SE", "https://www.amazon.se/")
