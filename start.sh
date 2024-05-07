#!/usr/bin/env bash
# Build and start IP Asset Explorer in Docker, then open it in your browser.
#
#   ./start.sh              start (builds on first run) and open http://localhost:8080
#   ./start.sh --port 9090  use another port
#   ./start.sh --lan        let other computers on your network open it too
#   ./start.sh --reset      delete all uploaded data and start again empty
#   ./start.sh --no-open    don't open a browser
#   ./start.sh stop         stop it (your data is kept)
set -euo pipefail
cd "$(dirname "$0")"

PORT="${PORT:-8080}"
BIND=127.0.0.1
RESET=0
OPEN=1
CMD=start
while [ $# -gt 0 ]; do
  case "$1" in
    --port) PORT="${2:?--port needs a number}"; shift 2 ;;
    --port=*) PORT="${1#*=}"; shift ;;
    --lan) BIND=0.0.0.0; shift ;;
    --reset) RESET=1; shift ;;
    --no-open) OPEN=0; shift ;;
    stop) CMD=stop; shift ;;
    -h|--help) sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown option: $1 (see ./start.sh --help)" >&2; exit 2 ;;
  esac
done
case "$PORT" in ''|*[!0-9]*) echo "--port must be a number" >&2; exit 2 ;; esac

say() { printf '\033[1m==>\033[0m %s\n' "$*"; }
die() { printf '\033[31mError:\033[0m %s\n' "$*" >&2; exit 1; }

command -v docker >/dev/null 2>&1 ||
  die "Docker is not installed. Install Docker Desktop from https://www.docker.com/products/docker-desktop/ and run this again."
docker compose version >/dev/null 2>&1 ||
  die "Docker Compose is missing. Update Docker Desktop, or install the docker-compose-plugin package."

if ! docker info >/dev/null 2>&1; then
  if [ "$(uname)" = Darwin ]; then
    say "Starting Docker Desktop..."
    open -a Docker 2>/dev/null || die "Could not start Docker Desktop. Start it yourself, then run this again."
  else
    die "Docker is not running. Start it (for example: sudo systemctl start docker), then run this again."
  fi
  for _ in $(seq 1 90); do docker info >/dev/null 2>&1 && break; sleep 1; done
  docker info >/dev/null 2>&1 || die "Docker did not start within 90 seconds."
fi

export PORT BIND
if [ "$CMD" = stop ]; then
  say "Stopping IP Asset Explorer..."
  docker compose down
  exit 0
fi
if [ "$RESET" = 1 ]; then
  say "Removing the container and all uploaded data..."
  docker compose down -v
fi

say "Building and starting (the first run downloads Python and takes a minute or two)..."
docker compose up -d --build ||
  die "Could not start the container. If port $PORT is already in use, try: ./start.sh --port 9090"

say "Waiting for the app to be ready..."
URL="http://localhost:${PORT}"
cid="$(docker compose ps -q explorer)"
ready=0
for _ in $(seq 1 120); do
  if curl -fsS -o /dev/null "$URL/healthz" 2>/dev/null; then ready=1; break; fi
  [ "$(docker inspect -f '{{.State.Running}}' "$cid" 2>/dev/null)" = true ] || break
  sleep 1
done
if [ "$ready" != 1 ]; then
  docker compose logs --tail 50 explorer >&2 || true
  die "The app did not become ready. The log above should say why."
fi

lan_ip() {
  if [ "$(uname)" = Darwin ]; then
    local ifc
    ifc="$(route -n get default 2>/dev/null | awk '/interface:/{print $2}')"
    for i in $ifc en0 en1; do
      ipconfig getifaddr "$i" 2>/dev/null && return
    done
  else
    hostname -I 2>/dev/null | awk '{print $1}'
  fi
}

echo
printf '  \033[1mIP Asset Explorer is running.\033[0m\n\n'
printf '  Open:  \033[4m%s\033[0m\n' "$URL"
if [ "$BIND" = 0.0.0.0 ]; then
  ip="$(lan_ip || true)"
  [ -n "$ip" ] && printf '  From other computers on your network:  \033[4mhttp://%s:%s\033[0m\n' "$ip" "$PORT"
fi
printf '\n  Stop it with ./start.sh stop. Your data is kept between runs.\n\n'
if [ "$OPEN" = 1 ]; then
  (open "$URL" 2>/dev/null || xdg-open "$URL" >/dev/null 2>&1 || true)
fi
