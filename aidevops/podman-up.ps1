# Start the AIDevOps stack on Podman (Windows). Run from the aidevops folder:  .\podman-up.ps1
#
# Needs Podman Desktop (https://podman-desktop.io) and a compose provider (docker-compose, or
# `pip install podman-compose`). The only engine-specific setting is the socket the metrics sidecar mounts.
$ErrorActionPreference = "Stop"

if (-not (Get-Command podman -ErrorAction SilentlyContinue)) {
    throw "Podman is not installed. Install Podman Desktop first (https://podman-desktop.io)."
}
if (-not (Test-Path "../.env")) {
    throw "../.env is missing. Copy ../.env.example to ../.env and add your Groq keys."
}

$running = (podman machine list --format "{{.Running}}") -contains "true"
if (-not $running) {
    Write-Host "Starting the Podman machine..."
    podman machine start
}

# Path of the engine's API socket *inside* the Podman machine, where the containers run.
$sock = ((podman info --format "{{.Host.RemoteSocket.Path}}").Trim()) -replace '^unix://', ''  # a mount needs the plain path
if (-not $sock) { throw "Could not find the Podman socket. Is the machine running?" }
Write-Host "Container engine socket: $sock"
$env:CONTAINER_SOCK = $sock

$env:PODMAN_COMPOSE_WARNING_LOGS = "false"   # hide "Executing external compose provider" (harmless, but PowerShell shows it as an error)
$ErrorActionPreference = "Continue"           # judge success by the exit code, not by anything printed to stderr
podman compose -f docker-compose.yml up --build -d
if ($LASTEXITCODE -ne 0) { throw "podman compose failed with exit code $LASTEXITCODE" }
Write-Host ""
Write-Host "Control plane: http://localhost:9000    Storefront: http://localhost:8080    Phone app: http://localhost:9000/m/"
