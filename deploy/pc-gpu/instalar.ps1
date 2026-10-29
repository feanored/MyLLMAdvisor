# Prepara, uma vez, um PC Windows com GPU AMD para servir o Ollama ao My LLM
# Advisor no fly.io (fly.gpu.toml). Instala Ollama, Caddy e Tailscale, baixa
# os modelos, gera o token, publica o proxy pelo Tailscale Funnel e agenda o
# iniciar.ps1 no logon. No fim, mostra o comando `fly secrets set` pronto.
#
#   powershell -ExecutionPolicy Bypass -File deploy\pc-gpu\instalar.ps1
#
# Pode rodar de novo sem problema: o que já existe é mantido.
$ErrorActionPreference = "Stop"

foreach ($id in "Ollama.Ollama", "CaddyServer.Caddy", "Tailscale.Tailscale") {
    winget install --id $id -e --accept-source-agreements --accept-package-agreements
}
# Os programas recém-instalados ainda não estão no PATH desta sessão.
$env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
            [Environment]::GetEnvironmentVariable("Path", "User")

# Token que o app no Fly manda em toda chamada (OLLAMA_API_KEY), fora do repositório.
$dir = Join-Path $env:LOCALAPPDATA "MyLLMAdvisor"
New-Item -ItemType Directory -Force $dir | Out-Null
$tokenFile = Join-Path $dir "ollama-token.txt"
if (-not (Test-Path $tokenFile)) {
    $bytes = New-Object byte[] 32
    [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    -join ($bytes | ForEach-Object { $_.ToString("x2") }) | Set-Content $tokenFile -NoNewline
}
$token = (Get-Content $tokenFile -Raw).Trim()

# Ollama no ar para baixar os modelos (os mesmos do OLLAMA_PULL_MODELS do fly.toml).
try { Invoke-RestMethod http://127.0.0.1:11434/api/version -TimeoutSec 2 | Out-Null }
catch { Start-Process ollama -ArgumentList "serve" -WindowStyle Hidden; Start-Sleep -Seconds 5 }
foreach ($model in "bge-m3", "qwen3:8b", "qwen3.5", "glm-ocr") {
    ollama pull $model
}

# Login no Tailscale (abre o navegador) e publica a porta do Caddy em HTTPS.
# Na 1ª vez, o funnel pode pedir para habilitar o Funnel na sua tailnet: abra
# o link que ele mostrar, aprove e rode este script de novo.
tailscale up
tailscale funnel --bg 11435

# iniciar.ps1 a cada logon, escondido.
$iniciar = Join-Path $PSScriptRoot "iniciar.ps1"
$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$iniciar`""
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries
Register-ScheduledTask -TaskName "MyLLMAdvisor Ollama" -Action $action -Trigger $trigger `
    -Settings $settings -Force | Out-Null
Start-ScheduledTask -TaskName "MyLLMAdvisor Ollama"

$dnsName = ((tailscale status --json | ConvertFrom-Json).Self.DNSName).TrimEnd(".")
Write-Host ""
Write-Host "Pronto. Na máquina com o repositório e o flyctl, rode:" -ForegroundColor Green
Write-Host ""
Write-Host "fly secrets set --app myllmadvisor-gpu OLLAMA_HOST=`"https://$dnsName`" OLLAMA_API_KEY=`"$token`""
Write-Host ""
Write-Host "Teste daqui mesmo (deve listar os modelos):"
Write-Host "curl.exe -H `"Authorization: Bearer $token`" https://$dnsName/api/tags"
