# Sobe o que o PC com GPU precisa para atender o My LLM Advisor no fly.io: o
# Ollama (se o app da bandeja ainda não estiver rodando) e o proxy Caddy com
# o token. O Tailscale Funnel já fica ligado sozinho (configurado uma vez
# pelo instalar.ps1). Roda a cada logon pela tarefa agendada do instalar.ps1.
$ErrorActionPreference = "Stop"

$tokenFile = Join-Path $env:LOCALAPPDATA "MyLLMAdvisor\ollama-token.txt"
if (-not (Test-Path $tokenFile)) {
    throw "Token não encontrado em $tokenFile. Rode antes o instalar.ps1."
}

function Test-Ollama {
    try { Invoke-RestMethod http://127.0.0.1:11434/api/version -TimeoutSec 2 | Out-Null; $true }
    catch { $false }
}

if (-not (Test-Ollama)) {
    Start-Process ollama -ArgumentList "serve" -WindowStyle Hidden
    while (-not (Test-Ollama)) { Start-Sleep -Seconds 1 }
}

$env:OLLAMA_API_KEY = (Get-Content $tokenFile -Raw).Trim()
caddy run --config (Join-Path $PSScriptRoot "Caddyfile") --adapter caddyfile
