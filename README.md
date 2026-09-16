# System Monitoring Dashboard

Dashboard realtime untuk monitoring **keseluruhan sistem** (bukan per-project). Pure `Python 3.12` stdlib, tanpa `pip`/`npm`.

No login/auth. Data di-refresh tiap 1 detik via SSE.

## Fitur

- **CPU:** total %, per-core %, loadavg 1/5/15, model CPU, MHz, history 60s
- **RAM:** total/used/available %, buffers/cached, swap
- **Storage:** semua mount (`/`, `/boot`, `/mnt`, `/boot/efi`, dll) — usage %, inodes, free/total, I/O `read/write rate` per device via `/proc/diskstats`
- **Network:** per interface (`eth0`/`lo`/docker `veth*`) — rx/tx bytes rate, packets, history chart
- **System:** hostname, OS, kernel, uptime, users, process count
- **Top Processes:** `ps -eo` sorted `CPU`/`MEM` (toggle), pid, ppid, comm, pcpu, pmem, cmd
- **Docker:** `docker ps -a` + `docker stats` cached 5s — semua container
- **Projects:** scan `$HOME/*` (configurable via `PROJECTS_ROOT`) — deteksi `docker/node/python/dotnet/minecraft/valheim/git`, size `du -sb`, git branch/status, `.service` status, running via docker/systemd match, search & filter realtime
- **Responsive 100%:** `320px → 1600px` (mobile/tablet/desktop), dark theme

## Teknologi

- Backend: `http.server ThreadingHTTPServer` + `SSE` (`/api/stream?interval=1`)
- Data source: `/proc/stat`, `/proc/meminfo`, `/proc/mounts` + `statvfs`, `/proc/diskstats`, `/proc/net/dev`, `/proc/uptime`, `ps`, `docker`
- Frontend: `Tailwind CDN` + `Chart.js 4` + `Font Awesome` — single page
- Tanpa dependency eksternal — `python3 app.py` langsung jalan

## Endpoint

- `GET /` → dashboard HTML
- `GET /static/*` → js/css
- `GET /api/health` → `{"status":"ok"}`
- `GET /api/stats` → JSON lengkap (cpu, memory, disks, disk_io, network, uptime, processes, docker, projects)
- `GET /api/stream` → `text/event-stream` SSE interval 1s (realtime)
- `GET /api/projects?refresh=1` → full sync scan
- `GET /api/docker` → docker cached
- `GET /api/processes?sort=cpu|mem` → top 15

## Cara pakai

```bash
# manual (scan $HOME)
python3 app.py --port 9090 --host 0.0.0.0

# scan folder lain
python3 app.py --port 9090 --projects-root /home/uniserver
# atau
PROJECTS_ROOT=/home/uniserver python3 app.py --port 9090

# atau via script
./start.sh
PORT=8080 ./start.sh

# systemd
sudo cp system-monitor.service /etc/systemd/system/
# sesuaikan User= dan WorkingDirectory= di file service, lalu:
sudo systemctl daemon-reload
sudo systemctl enable --now system-monitor
sudo systemctl status system-monitor
journalctl -u system-monitor -f
```

Docker (optional):

```bash
docker build -t system-monitor .
docker run -d -p 9090:9090 -v /proc:/proc:ro -v /home:/home:ro -v /var/run/docker.sock:/var/run/docker.sock system-monitor
```

## Akses dari laptop (VPS dev, port publik tertutup)

SSH tunnel (aman, tanpa buka port publik):

```bash
# di laptop
ssh -N -L 9090:localhost:9090 uniserver@<VPS-IP>
```

Biarkan terminal SSH menyala, lalu buka `http://localhost:9090/` di browser laptop.

## Port

Default `9090`, auto cari `9090-9110` jika terpakai. Tidak perlu auth.

```bash
ss -tlnp | grep 9090
curl http://127.0.0.1:9090/api/health
```

## Struktur

```
system-monitor/
  app.py                  # backend + collectors
  static/
    index.html            # dashboard UI
    app.js                # SSE + Chart.js logic
    style.css             # scrollbar & badges
  start.sh                # helper script
  system-monitor.service  # systemd unit (sesuaikan User/WorkingDirectory)
  README.md
```

## Catatan

- Projects scan `du -sb` cached 30s async (pertama placeholder `...` lalu real size di background)
- Docker stats cached 5s
- `disk list` filter `tmpfs` kecuali `/`, `/mnt`, dll
- Untuk reverse proxy: `proxy_pass http://127.0.0.1:9090;` (nginx/caddy)
