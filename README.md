# System Monitoring Dashboard

A realtime dashboard for monitoring the **entire system** (not per-project). Pure Python stdlib, no `pip`/`npm`.

No login/auth. Data refreshes every 1 second via SSE.

## Requirements

- **OS:** Linux & Windows (auto-adapts natively without extra configuration)
- **Python:** `3.8+` (pure standard library only, zero `pip` install required)
- **Optional** (graceful fallback if missing): `docker`, `git`, `systemctl`, `who`
- **Frontend:** internet access required for CDNs (`Tailwind`, `Chart.js`, `Font Awesome`)

## Features

- **CPU:** total %, per-core %, loadavg 1/5/15, CPU model, MHz, 60s history
- **RAM:** total/used/available %, buffers/cached, swap
- **Storage:** all mounts (`/`, `/boot`, `/mnt`, `/boot/efi`, etc.) — usage %, inodes, free/total, I/O `read/write rate` per device via `/proc/diskstats`
- **Network:** per interface (`eth0`/`lo`/docker `veth*`) — rx/tx byte rate, packets, history chart
- **System:** hostname, OS, kernel, uptime, users, process count
- **Top Processes:** `ps -eo` sorted by `CPU`/`MEM` (toggle), pid, ppid, comm, pcpu, pmem, cmd
- **Docker:** `docker ps -a` + `docker stats` cached 5s — all containers
- **Projects:** scans `$HOME/*` (configurable via `PROJECTS_ROOT`) — detects `docker/node/python/dotnet/minecraft/valheim/git`, size via `du -sb`, git branch/status, `.service` status, running state via docker/systemd match, realtime search & filter
- **100% responsive:** `320px → 1600px` (mobile/tablet/desktop), dark theme

## Tech

- Backend: `http.server ThreadingHTTPServer` + `SSE` (`/api/stream?interval=1`)
- Data sources: `/proc/stat`, `/proc/meminfo`, `/proc/mounts` + `statvfs`, `/proc/diskstats`, `/proc/net/dev`, `/proc/uptime`, `ps`, `docker`
- Frontend: `Tailwind CDN` + `Chart.js 4` + `Font Awesome` — single page
- Zero external dependencies — just run `python3 app.py`

## Endpoints

- `GET /` → dashboard HTML
- `GET /static/*` → js/css
- `GET /api/health` → `{"status":"ok"}`
- `GET /api/stats` → full JSON (cpu, memory, disks, disk_io, network, uptime, processes, docker, projects)
- `GET /api/stream` → `text/event-stream` SSE at 1s interval (realtime)
- `GET /api/projects?refresh=1` → full sync scan
- `GET /api/docker` → cached docker info
- `GET /api/processes?sort=cpu|mem` → top 15

## Usage

```bash
git clone https://github.com/haikalthrq/System-Monitoring-Dashboard.git
cd System-Monitoring-Dashboard

# Linux
python3 app.py --port 9090 --host 0.0.0.0
# or via script
./start.sh

# Windows (Command Prompt / PowerShell)
python app.py --port 9090 --host 0.0.0.0
# or via scripts
.\start.bat
.\start.ps1

# scan a different folder
python app.py --port 9090 --projects-root "D:\My Files\Personal Project"
```

systemd:

```bash
sudo ./install-service.sh            # default port 9090
sudo ./install-service.sh --port=8080
journalctl -u system-monitor -f
```

Docker:

```bash
docker build -t system-monitor .
docker run -d --name system-monitor -p 9090:9090 \
  -v /proc:/proc:ro -v /home:/home:ro \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -e PROJECTS_ROOT=/home \
  system-monitor
```

## Access from a laptop (dev VPS, public port closed)

SSH tunnel (secure, no need to open a public port):

```bash
# on the laptop
ssh -N -L 9090:localhost:9090 user@<VPS-IP>
```

Keep the SSH terminal open, then open `http://localhost:9090/` in the laptop browser.

## Port

Default `9090`, auto-searches `9090-9110` if taken. No auth required.

```bash
ss -tlnp | grep 9090
curl http://127.0.0.1:9090/api/health
```

## Structure

```
.
  app.py                  # backend + collectors
  static/
    index.html            # dashboard UI
    app.js                # SSE + Chart.js logic
    style.css             # scrollbar & badges
  start.sh                # helper script
  install-service.sh      # systemd installer (auto-fills User/paths)
  system-monitor.service  # unit template (do not copy manually)
  Dockerfile              # optional image
  README.md
```

## Notes

- Project scan uses `du -sb` cached for 30s async (placeholder `...` first, then real sizes in the background)
- Docker stats cached for 5s
- `disk list` filters `tmpfs` except `/`, `/mnt`, etc.
- For reverse proxy: `proxy_pass http://127.0.0.1:9090;` (nginx/caddy)
