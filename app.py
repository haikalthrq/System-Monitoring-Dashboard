#!/usr/bin/env python3
"""
System Monitor - Realtime Dashboard
Pure stdlib, no pip required. Python 3.12+
Monitors: CPU, RAM, Disk, Network, Processes, Docker, Projects, Uptime
"""
import os
import re
import json
import time
import glob
import shutil
import subprocess
import threading
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from pathlib import Path

ROOT = Path(__file__).parent
STATIC_DIR = ROOT / "static"
# Project scan root: configurable via env PROJECTS_ROOT, default to current user's home.
# Example: PROJECTS_ROOT=$HOME python3 app.py
HOME_DIR = Path(os.environ.get("PROJECTS_ROOT") or Path.home())

# Global state for delta calculations
_state = {
    "cpu_prev": None,
    "cpu_prev_per_core": {},
    "net_prev": {},
    "disk_io_prev": {},
    "last_collect": 0,
    "cached": None,
}

def read_file(path, default=""):
    try:
        with open(path, 'r') as f:
            return f.read()
    except:
        return default

def get_cpu_stats():
    """Returns overall cpu percent, per-core, loadavg, cores count"""
    try:
        line = read_file("/proc/stat").splitlines()[0]
        # cpu  user nice system idle iowait irq softirq steal guest guest_nice
        parts = line.split()
        vals = list(map(int, parts[1:8]))  # first 7
        total = sum(vals)
        idle = vals[3] + vals[4]  # idle + iowait
        prev = _state["cpu_prev"]
        if prev is None:
            cpu_percent = 0
        else:
            prev_total, prev_idle = prev
            diff_total = total - prev_total
            diff_idle = idle - prev_idle
            if diff_total > 0:
                cpu_percent = 100 * (1 - diff_idle / diff_total)
            else:
                cpu_percent = 0
        _state["cpu_prev"] = (total, idle)

        # per core
        per_core = []
        per_core_detail = []
        for l in read_file("/proc/stat").splitlines():
            if l.startswith("cpu") and l[3] != " " and l[3].isdigit():
                p = l.split()
                c_vals = list(map(int, p[1:8]))
                c_total = sum(c_vals)
                c_idle = c_vals[3] + c_vals[4]
                core_id = p[0]
                prev_c = _state["cpu_prev_per_core"].get(core_id)
                if prev_c is None:
                    pct = 0
                else:
                    pt, pi = prev_c
                    dt = c_total - pt
                    di = c_idle - pi
                    pct = 100 * (1 - di / dt) if dt > 0 else 0
                _state["cpu_prev_per_core"][core_id] = (c_total, c_idle)
                per_core.append(round(max(0, min(100, pct)), 1))
                per_core_detail.append({"core": core_id, "usage": round(max(0, min(100, pct)), 1)})
        
        # cpu info
        cpuinfo = read_file("/proc/cpuinfo")
        model = "Unknown"
        cores = 0
        for l in cpuinfo.splitlines():
            if "model name" in l:
                model = l.split(":",1)[1].strip()
                break
        cores = cpuinfo.count("processor")
        mhz = "0"
        for l in cpuinfo.splitlines():
            if "cpu MHz" in l:
                mhz = l.split(":",1)[1].strip()
                break

        loadavg = read_file("/proc/loadavg").strip().split()[:3]
        loadavg = [float(x) for x in loadavg] if len(loadavg)==3 else [0,0,0]

        # uptime handled separately
        return {
            "usage": round(max(0, min(100, cpu_percent)), 1),
            "per_core": per_core,
            "per_core_detail": per_core_detail,
            "cores": cores if cores else len(per_core) if per_core else 1,
            "model": model,
            "mhz": mhz,
            "loadavg": loadavg,
        }
    except Exception as e:
        return {"usage": 0, "per_core": [], "per_core_detail": [], "cores": 0, "model": str(e), "mhz": "0", "loadavg": [0,0,0]}

def get_memory():
    try:
        mem = {}
        for l in read_file("/proc/meminfo").splitlines():
            if ":" in l:
                k,v = l.split(":",1)
                # value in kB
                num = re.findall(r'\d+', v)
                if num:
                    mem[k.strip()] = int(num[0]) * 1024  # bytes
        total = mem.get("MemTotal", 0)
        free = mem.get("MemFree", 0)
        available = mem.get("MemAvailable", 0)
        buffers = mem.get("Buffers", 0)
        cached = mem.get("Cached", 0)
        swap_total = mem.get("SwapTotal", 0)
        swap_free = mem.get("SwapFree", 0)
        used = total - free - buffers - cached if total else 0
        # more accurate used = total - available is also popular
        used_alt = total - available if available else used

        # Use used_alt for display (actual used memory)
        # percent
        percent = (used_alt / total * 100) if total else 0

        return {
            "total": total,
            "free": free,
            "available": available,
            "used": used_alt,
            "buffers": buffers,
            "cached": cached,
            "swap_total": swap_total,
            "swap_free": swap_free,
            "swap_used": swap_total - swap_free,
            "percent": round(percent,1),
            "swap_percent": round((swap_total - swap_free)/swap_total*100,1) if swap_total else 0,
        }
    except Exception as e:
        return {"total":0,"free":0,"available":0,"used":0,"buffers":0,"cached":0,"swap_total":0,"swap_free":0,"swap_used":0,"percent":0,"swap_percent":0,"error":str(e)}

def get_disks():
    """Disks via /proc/mounts + statvfs, filter real filesystems"""
    disks = []
    try:
        # Use shutil.disk_usage for main mounts plus parse /proc/mounts
        exclude_fs = {"tmpfs","devtmpfs","efivarfs","squashfs","overlay","cgroup","cgroup2","proc","sysfs","devpts","securityfs","pstore","bpf"}
        exclude_mount = {"/dev/shm","/run/lock","/sys/firmware/efi/efivars"}
        seen = set()
        for line in read_file("/proc/mounts").splitlines():
            parts = line.split()
            if len(parts) < 3: continue
            dev, mnt, fstype = parts[0], parts[1], parts[2]
            if fstype in exclude_fs: 
                # still allow / and /mnt if needed, but skip small tmpfs
                if mnt not in ["/","/mnt","/boot","/boot/efi"]:
                    continue
            if mnt in exclude_mount:
                continue
            if mnt in seen:
                continue
            seen.add(mnt)
            try:
                usage = shutil.disk_usage(mnt)
                # inode info via statvfs
                st = os.statvfs(mnt)
                total_inodes = st.f_files
                free_inodes = st.f_ffree
                used_inodes = total_inodes - free_inodes
                inode_percent = (used_inodes/total_inodes*100) if total_inodes else 0
                percent = (usage.used / usage.total * 100) if usage.total else 0
                disks.append({
                    "device": dev,
                    "mount": mnt,
                    "fstype": fstype,
                    "total": usage.total,
                    "used": usage.used,
                    "free": usage.free,
                    "percent": round(percent,1),
                    "inodes_total": total_inodes,
                    "inodes_used": used_inodes,
                    "inodes_free": free_inodes,
                    "inodes_percent": round(inode_percent,1),
                })
            except:
                continue
        # Sort: root first, then by mount
        disks.sort(key=lambda x: (0 if x["mount"]=="/" else 1, x["mount"]))
        return disks
    except Exception as e:
        return [{"error": str(e)}]

def get_disk_io():
    """Read /proc/diskstats, calculate r/w bytes per sec"""
    try:
        current = {}
        for line in read_file("/proc/diskstats").splitlines():
            parts = line.split()
            if len(parts) < 14: continue
            dev = parts[2]
            # Skip loop, ram
            if dev.startswith("loop") or dev.startswith("ram"): continue
            reads = int(parts[5])  # sectors read
            writes = int(parts[9]) # sectors written
            # sectors are 512 bytes
            r_bytes = reads * 512
            w_bytes = writes * 512
            # io ticks? use time?
            current[dev] = (r_bytes, w_bytes)
        # calc delta per sec
        now = time.time()
        prev = _state["disk_io_prev"]
        prev_time = _state.get("disk_io_time", now)
        delta_t = now - prev_time if prev_time else 1
        if delta_t < 0.1: delta_t = 1
        result = []
        for dev, (rb, wb) in current.items():
            if dev in prev:
                prb, pwb = prev[dev]
                r_rate = (rb - prb) / delta_t
                w_rate = (wb - pwb) / delta_t
                # filter out small virtual devices with zero stats long time? Keep only sda,sdb,vda etc and not too many
                # Keep all but sort
                result.append({"device": dev, "read_bytes": rb, "write_bytes": wb, "read_rate": max(0, r_rate), "write_rate": max(0, w_rate)})
            else:
                result.append({"device": dev, "read_bytes": rb, "write_bytes": wb, "read_rate": 0, "write_rate": 0})
        _state["disk_io_prev"] = current
        _state["disk_io_time"] = now
        # prefer sda, sdb, vda, nvme
        result.sort(key=lambda x: x["device"])
        return result
    except Exception as e:
        return [{"error": str(e)}]

def get_network():
    try:
        current = {}
        for line in read_file("/proc/net/dev").splitlines():
            if ":" not in line: continue
            if "lo" in line and "face" in line: continue
            # header lines contain |
            if "|" in line: continue
            iface, data = line.split(":",1)
            iface = iface.strip()
            if iface == "lo":
                # include lo but track separately
                pass
            fields = data.split()
            if len(fields) < 16: continue
            rx_bytes = int(fields[0])
            tx_bytes = int(fields[8])
            rx_packets = int(fields[1])
            tx_packets = int(fields[9])
            current[iface] = (rx_bytes, tx_bytes, rx_packets, tx_packets)
        now = time.time()
        prev = _state["net_prev"]
        prev_time = _state.get("net_time", now)
        delta_t = now - prev_time if prev_time else 1
        if delta_t < 0.1: delta_t = 1
        result = []
        for iface, (rx, tx, rxp, txp) in current.items():
            if iface in prev:
                prx, ptx, prxp, ptxp = prev[iface]
                rx_rate = (rx - prx) / delta_t
                tx_rate = (tx - ptx) / delta_t
                rxp_rate = (rxp - prxp) / delta_t
                txp_rate = (txp - ptxp) / delta_t
                result.append({
                    "iface": iface,
                    "rx_bytes": rx,
                    "tx_bytes": tx,
                    "rx_rate": max(0, rx_rate),
                    "tx_rate": max(0, tx_rate),
                    "rx_packets": rxp,
                    "tx_packets": txp,
                    "rx_p_rate": max(0, rxp_rate),
                    "tx_p_rate": max(0, txp_rate),
                })
            else:
                result.append({
                    "iface": iface,
                    "rx_bytes": rx,
                    "tx_bytes": tx,
                    "rx_rate": 0,
                    "tx_rate": 0,
                    "rx_packets": rxp,
                    "tx_packets": txp,
                    "rx_p_rate": 0,
                    "tx_p_rate": 0,
                })
        _state["net_prev"] = current
        _state["net_time"] = now
        # sort eth0 first
        result.sort(key=lambda x: (0 if x["iface"]=="eth0" else 1 if x["iface"]=="ens3" else 2, x["iface"]))
        return result
    except Exception as e:
        return [{"error": str(e)}]

def get_uptime():
    try:
        up = read_file("/proc/uptime").split()
        secs = float(up[0]) if up else 0
        # boot time? not needed
        # users
        users = 0
        try:
            out = subprocess.check_output(["who"], text=True, timeout=1)
            users = len(out.strip().splitlines()) if out.strip() else 0
        except:
            pass
        # hostname
        hostname = read_file("/proc/sys/kernel/hostname").strip() or os.uname().nodename
        # os
        os_pretty = read_file("/etc/os-release")
        pretty = "Ubuntu"
        for l in os_pretty.splitlines():
            if l.startswith("PRETTY_NAME="):
                pretty = l.split("=",1)[1].strip().strip('"')
                break
        kernel = os.uname().release
        # loadavg already in cpu but duplicate
        loadavg = read_file("/proc/loadavg").strip().split()[:3]
        return {
            "uptime_seconds": secs,
            "uptime_human": human_duration(secs),
            "users": users,
            "hostname": hostname,
            "os": pretty,
            "kernel": kernel,
            "loadavg": loadavg,
        }
    except Exception as e:
        return {"uptime_seconds":0,"uptime_human":"0s","users":0,"hostname":"unknown","os":str(e),"kernel":""}

def human_duration(secs):
    secs = int(secs)
    days = secs // 86400
    hours = (secs % 86400) // 3600
    mins = (secs % 3600) // 60
    if days: return f"{days}d {hours}h {mins}m"
    if hours: return f"{hours}h {mins}m"
    return f"{mins}m {secs%60}s"

def get_processes(limit=10, sort_by="cpu"):
    """Top processes by cpu or mem, reading /proc/*/stat & status"""
    procs = []
    try:
        # Use ps if available for simplicity and accuracy (fallback to /proc)
        # Try ps
        try:
            out = subprocess.check_output(
                ["ps", "-eo", "pid,ppid,comm,pcpu,pmem,etime,stat,cmd", "--sort=-%cpu"],
                text=True, timeout=2
            )
            lines = out.strip().splitlines()[1:]  # skip header
            for l in lines[:limit]:
                # pid ppid comm pcpu pmem etime stat cmd
                # comm is without args, cmd is full
                # Use regex: pid, ppid, comm, pcpu, pmem, etime, stat, cmd(rest)
                parts = l.strip().split(None, 7)
                if len(parts) < 7: continue
                pid, ppid, comm, pcpu, pmem, etime, stat = parts[:7]
                cmd = parts[7] if len(parts) > 7 else comm
                # Truncate cmd
                if len(cmd) > 80: cmd = cmd[:80]+"..."
                procs.append({
                    "pid": int(pid),
                    "ppid": int(ppid),
                    "name": comm,
                    "cpu": float(pcpu),
                    "mem": float(pmem),
                    "etime": etime,
                    "stat": stat,
                    "cmd": cmd,
                })
            # Always fetch both sorted lists for completeness
            try:
                out_mem = subprocess.check_output(
                    ["ps", "-eo", "pid,ppid,comm,pcpu,pmem,etime,stat,cmd", "--sort=-%mem"],
                    text=True, timeout=2
                )
                lines_mem = out_mem.strip().splitlines()[1:]
                procs_mem = []
                for l in lines_mem[:limit]:
                    parts = l.strip().split(None, 7)
                    if len(parts) < 7: continue
                    pid, ppid, comm, pcpu, pmem, etime, stat = parts[:7]
                    cmd = parts[7] if len(parts) > 7 else comm
                    if len(cmd) > 80: cmd = cmd[:80]+"..."
                    procs_mem.append({
                        "pid": int(pid), "ppid": int(ppid), "name": comm,
                        "cpu": float(pcpu), "mem": float(pmem), "etime": etime, "stat": stat, "cmd": cmd
                    })
            except Exception:
                # fallback: sort procs copy by mem
                procs_mem = sorted(procs, key=lambda x: x["mem"], reverse=True)[:limit]
            return {"by_cpu": procs, "by_mem": procs_mem, "total": count_processes()}
        except Exception as e:
            # fallback to /proc scan (fresh list)
            fallback = []
            for pid_dir in glob.glob("/proc/[0-9]*"):
                try:
                    pid = int(os.path.basename(pid_dir))
                    stat = read_file(os.path.join(pid_dir, "stat"))
                    status = read_file(os.path.join(pid_dir, "status"))
                    cmdline = read_file(os.path.join(pid_dir, "cmdline")).replace("\x00"," ").strip()
                    if not stat: continue
                    m = re.match(r'(\d+) \((.+)\) (\w) (\d+)', stat)
                    if not m: continue
                    name = m.group(2)
                    ppid = int(m.group(4))
                    vmrss = 0
                    for l in status.splitlines():
                        if l.startswith("VmRSS:"):
                            vmrss = int(re.findall(r'\d+', l)[0]) * 1024 if re.findall(r'\d+', l) else 0
                    fallback.append({"pid": pid, "ppid": ppid, "name": name, "cpu": 0, "mem": round(vmrss/ (get_memory()["total"] or 1)*100,1), "etime":"?", "stat":"?", "cmd": cmdline or name})
                except:
                    continue
            fallback.sort(key=lambda x: x["mem"], reverse=True)
            # for fallback, by_cpu also mem-sorted (cpu unavailable)
            return {"by_cpu": fallback[:limit], "by_mem": fallback[:limit], "total": len(fallback)}
    except Exception as e:
        return {"by_cpu": [], "by_mem": [], "total": 0, "error": str(e)}

def count_processes():
    try:
        return len(glob.glob("/proc/[0-9]*"))
    except:
        return 0

def get_docker():
    """docker ps + stats if available"""
    containers = []
    try:
        # Use docker ps --format json if docker exists
        if not shutil.which("docker"):
            return {"available": False, "containers": []}
        out = subprocess.check_output(
            ["docker", "ps", "-a", "--format", "{{json .}}"],
            text=True, timeout=3
        )
        for line in out.strip().splitlines():
            if not line.strip(): continue
            try:
                j = json.loads(line)
                # Normalize fields: Image, Names, Status, State, Ports
                containers.append({
                    "id": j.get("ID","")[:12],
                    "image": j.get("Image",""),
                    "name": j.get("Names",""),
                    "status": j.get("Status",""),
                    "state": j.get("State",""),
                    "ports": j.get("Ports",""),
                    "created": j.get("CreatedAt",""),
                })
            except:
                continue
        # Try docker stats (no-stream) for running containers cpu/mem
        if containers:
            try:
                stats_out = subprocess.check_output(
                    ["docker", "stats", "--no-stream", "--format", "{{json .}}"],
                    text=True, timeout=2
                )
                stats_map = {}
                for line in stats_out.strip().splitlines():
                    try:
                        j = json.loads(line)
                        # Name, CPUPerc, MemUsage, MemPerc, NetIO, BlockIO
                        stats_map[j.get("Name","")] = j
                    except:
                        continue
                for c in containers:
                    s = stats_map.get(c["name"])
                    if s:
                        c["cpu"] = s.get("CPUPerc","-")
                        c["mem_usage"] = s.get("MemUsage","-")
                        c["mem_perc"] = s.get("MemPerc","-")
                        c["net_io"] = s.get("NetIO","-")
                        c["block_io"] = s.get("BlockIO","-")
            except Exception as e:
                pass
        return {"available": True, "containers": containers, "count": len(containers), "running": len([c for c in containers if "Up" in c.get("status","")])}
    except subprocess.CalledProcessError:
        return {"available": True, "containers": [], "count":0, "running":0}
    except Exception as e:
        return {"available": False, "containers": [], "error": str(e)}

def detect_project_type(path: Path):
    """Detect project type based on files"""
    types = []
    # check files - use glob with depth limit for nested projects
    def has_file_glob(pattern):
        try:
            # limit to 3 levels deep to avoid heavy scan
            return any(path.glob(pattern))
        except:
            return False
    if (path / "docker-compose.yml").exists() or (path / "infra" / "docker-compose.yml").exists() or (path / "compose.yml").exists() or has_file_glob("**/docker-compose.yml"):
        types.append("docker")
    if (path / "Dockerfile").exists() or has_file_glob("**/Dockerfile"):
        if "docker" not in types: types.append("docker")
    if (path / "package.json").exists() or has_file_glob("*/package.json") or has_file_glob("*/*/package.json"):
        types.append("node")
    if (path / "requirements.txt").exists() or (path / "pyproject.toml").exists() or (path / "uv.lock").exists() or (path / "bot.py").exists() or has_file_glob("**/requirements.txt") or has_file_glob("**/pyproject.toml"):
        types.append("python")
    if list(path.glob("*.csproj")) or has_file_glob("**/*.csproj"):
        types.append("dotnet")
    if (path / "server.jar").exists() or (path / "server.properties").exists():
        types.append("minecraft")
    if (path / "start-server.sh").exists() or (path / "valheim.service").exists() or (path / "server" / "valheim_server.x86_64").exists():
        types.append("valheim")
    if (path / ".git").exists():
        types.append("git")
    # Generic
    if not types:
        # check for any known
        if any((path / f).exists() for f in ["apps","src","services","infra"]):
            types.append("monorepo")
        else:
            types.append("generic")
    return types

def _get_projects_sync(docker_info=None):
    """Sync scan, internal"""
    projects = []
    try:
        home = HOME_DIR
        # Get all dirs in home that are directories and not hidden except important
        candidates = []
        for p in home.iterdir():
            if p.is_dir():
                name = p.name
                # skip hidden except maybe .config but not needed
                if name.startswith("."):
                    continue
                # skip obvious non-projects
                if name in {"snap","tmp"}:
                    continue
                candidates.append(p)
        # Also scan for more via find? but this is enough
        for path in sorted(candidates, key=lambda x: x.name.lower()):
            try:
                # du size (fast via statvfs or du command)
                # Use du -sb if available, with timeout 2s
                size_bytes = 0
                size_human = "-"
                try:
                    out = subprocess.check_output(["du","-sb", str(path)], text=True, timeout=2)
                    size_bytes = int(out.split()[0])
                    size_human = human_bytes(size_bytes)
                except:
                    try:
                        # fallback: sum? skip
                        size_bytes = 0
                    except:
                        pass
                # git info
                git_branch = ""
                git_status = ""
                if (path / ".git").exists():
                    try:
                        branch = subprocess.check_output(["git","-C", str(path), "branch","--show-current"], text=True, timeout=1).strip()
                        git_branch = branch
                        # status --porcelain count
                        status_out = subprocess.check_output(["git","-C", str(path), "status","--porcelain"], text=True, timeout=1)
                        dirty = len(status_out.strip().splitlines()) if status_out.strip() else 0
                        git_status = f"{dirty} changes" if dirty else "clean"
                    except:
                        pass
                # service file
                services = []
                for svc in path.glob("*.service"):
                    services.append(svc.name)
                # Check if systemd service active?
                service_active = {}
                for svc in services:
                    try:
                        out = subprocess.check_output(["systemctl","is-active", svc], text=True, timeout=1).strip()
                        service_active[svc] = out
                    except:
                        service_active[svc] = "unknown"
                # Docker related? check if any container name contains project name lower
                docker_hint = ""
                # types
                types = detect_project_type(path)
                # last modified
                try:
                    mtime = path.stat().st_mtime
                    last_mod = time.strftime("%Y-%m-%d %H:%M", time.localtime(mtime))
                except:
                    last_mod = ""
                # Check if running via docker OR systemd
                running = False
                try:
                    di = docker_info if docker_info is not None else {}
                    for c in di.get("containers", []):
                        if path.name.lower() in c["name"].lower() or path.name.lower().replace("-","") in c["name"].lower():
                            if "Up" in c["status"]:
                                running = True
                                break
                    # also check systemd service active
                    if not running:
                        for svc, st in service_active.items():
                            if st == "active":
                                running = True
                                break
                    # extra check for java/valheim processes via ps name match (minecraft java, valheim_server)
                    if not running and path.name.lower() in ["minecraft-host","valheim","valheim-host","valheim-test","valheim-mod-test"]:
                        try:
                            # quick ps check without heavy call - use cached procs? fallback
                            out = subprocess.check_output(["ps","-eo","comm"], text=True, timeout=1)
                            if "java" in out and "minecraft" in path.name.lower():
                                running = True
                            if "valheim" in out.lower() and "valheim" in path.name.lower():
                                running = True
                        except:
                            pass
                except:
                    pass
                projects.append({
                    "name": path.name,
                    "path": str(path),
                    "types": types,
                    "size_bytes": size_bytes,
                    "size_human": size_human,
                    "git_branch": git_branch,
                    "git_status": git_status,
                    "services": services,
                    "service_active": service_active,
                    "last_modified": last_mod,
                    "running": running,
                })
            except Exception as e:
                projects.append({"name": path.name, "path": str(path), "types":["error"], "error": str(e)})
        # Sort: running first, then by name
        projects.sort(key=lambda x: (0 if x.get("running") else 1, x["name"].lower()))
        return projects
    except Exception as e:
        return [{"name":"error","error": str(e)}]

def human_bytes(b):
    if b is None: return "-"
    for unit in ['B','KB','MB','GB','TB']:
        if abs(b) < 1024.0:
            return f"{b:.1f} {unit}" if unit!='B' else f"{b} B"
        b /= 1024.0
    return f"{b:.1f} PB"

# Async projects cache refresh
def _refresh_projects_cache(docker_info=None):
    try:
        projects = _get_projects_sync(docker_info=docker_info)
        _state["projects_cache"] = projects
        _state["projects_cache_time"] = time.time()
    except Exception as e:
        _state["projects_cache"] = [{"name":"error","error": str(e)}]

def get_projects():
    """Public wrapper, returns cached or triggers async refresh"""
    now = time.time()
    cache = _state.get("projects_cache")
    cache_time = _state.get("projects_cache_time", 0)
    # If no cache or expired >30s, trigger background refresh and return stale/empty
    if cache is None or now - cache_time > 30:
        # If we have docker cache, pass it to avoid extra docker call
        docker_info = _state.get("docker_cache")
        # Start refresh thread only if not already running
        if not _state.get("projects_refreshing"):
            _state["projects_refreshing"] = True
            def do_refresh():
                try:
                    _refresh_projects_cache(docker_info=docker_info)
                finally:
                    _state["projects_refreshing"] = False
            threading.Thread(target=do_refresh, daemon=True).start()
        # Return stale cache if exists, else placeholder
        if cache is not None:
            return cache
        # Quick lightweight placeholder without size calc (fast)
        # HOME_DIR may not exist (e.g. docker -e PROJECTS_ROOT without mount)
        try:
            return [{"name": p.name, "path": str(p), "types": detect_project_type(p), "size_human": "...", "git_branch":"", "git_status":"loading...", "services":[], "service_active":{}, "last_modified":"", "running": False}
                    for p in sorted([x for x in HOME_DIR.iterdir() if x.is_dir() and not x.name.startswith(".") and x.name not in {"snap","tmp"}], key=lambda x: x.name.lower())]
        except Exception:
            return []
    return cache

# Docker cache
def get_docker_cached():
    now = time.time()
    if _state.get("docker_cache") is None or now - _state.get("docker_cache_time",0) > 5:
        d = get_docker()
        _state["docker_cache"] = d
        _state["docker_cache_time"] = now
        return d
    return _state["docker_cache"]

def collect_all():
    """Collect all stats at once"""
    # CPU first to ensure delta works
    cpu = get_cpu_stats()
    mem = get_memory()
    disks = get_disks()
    disk_io = get_disk_io()
    net = get_network()
    uptime = get_uptime()
    procs = get_processes(limit=8)
    docker = get_docker_cached()
    # projects cached async
    now = time.time()
    global _state
    projects = get_projects()

    data = {
        "timestamp": now,
        "timestamp_human": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)),
        "cpu": cpu,
        "memory": mem,
        "disks": disks,
        "disk_io": disk_io,
        "network": net,
        "uptime": uptime,
        "processes": procs,
        "docker": docker,
        "projects": projects,
    }
    _state["cached"] = data
    return data

# HTTP Handler
class MonitorHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # quiet
        return

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        # CORS
        cors_headers = {
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type",
        }

        if path == "/api/stats" or path == "/api/metrics":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            for k,v in cors_headers.items(): self.send_header(k,v)
            self.end_headers()
            data = collect_all()
            self.wfile.write(json.dumps(data).encode())
            return

        if path == "/api/stream" or path == "/api/sse":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("X-Accel-Buffering", "no")
            for k,v in cors_headers.items(): self.send_header(k,v)
            self.end_headers()
            try:
                # Send initial
                interval = float(qs.get("interval",["1"])[0])
                if interval < 0.5: interval = 0.5
                if interval > 10: interval = 10
                # Prime collectors (first call gives 0 for cpu/net)
                collect_all()
                time.sleep(0.5)
                while True:
                    data = collect_all()
                    payload = f"data: {json.dumps(data)}\n\n"
                    self.wfile.write(payload.encode())
                    self.wfile.flush()
                    time.sleep(interval)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                return
            except Exception as e:
                try:
                    self.wfile.write(f"data: {json.dumps({'error': str(e)})}\n\n".encode())
                except:
                    pass
                return

        if path == "/api/projects":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            for k,v in cors_headers.items(): self.send_header(k,v)
            self.end_headers()
            # ?refresh=1 forces sync scan
            if qs.get("refresh", ["0"])[0] == "1":
                projects = _get_projects_sync(docker_info=get_docker_cached())
            else:
                projects = get_projects()
            self.wfile.write(json.dumps({"projects": projects}).encode())
            return

        if path == "/api/docker":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            for k,v in cors_headers.items(): self.send_header(k,v)
            self.end_headers()
            self.wfile.write(json.dumps(get_docker_cached()).encode())
            return

        if path == "/api/processes":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            for k,v in cors_headers.items(): self.send_header(k,v)
            self.end_headers()
            sort = qs.get("sort",["cpu"])[0]
            self.wfile.write(json.dumps(get_processes(limit=15, sort_by=sort)).encode())
            return

        if path == "/api/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            for k,v in cors_headers.items(): self.send_header(k,v)
            self.end_headers()
            self.wfile.write(json.dumps({"status":"ok","time": time.time()}).encode())
            return

        # Static files
        if path == "/" or path == "/index.html":
            fp = STATIC_DIR / "index.html"
            if fp.exists():
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                for k,v in cors_headers.items(): self.send_header(k,v)
                self.end_headers()
                self.wfile.write(fp.read_bytes())
                return
        if path.startswith("/static/"):
            rel = path[len("/static/"):]
            fp = STATIC_DIR / rel
            if fp.exists() and fp.is_file() and str(fp.resolve()).startswith(str(STATIC_DIR.resolve())):
                ctype = "text/plain"
                if fp.suffix == ".js": ctype = "application/javascript"
                elif fp.suffix == ".css": ctype = "text/css"
                elif fp.suffix == ".html": ctype = "text/html"
                elif fp.suffix == ".json": ctype = "application/json"
                elif fp.suffix == ".svg": ctype = "image/svg+xml"
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                for k,v in cors_headers.items(): self.send_header(k,v)
                self.end_headers()
                self.wfile.write(fp.read_bytes())
                return
        # Try static directly (for /app.js etc)
        if path.startswith("/") and (STATIC_DIR / path.lstrip("/")).exists():
            fp = STATIC_DIR / path.lstrip("/")
            if fp.is_file():
                ctype = "text/plain"
                if fp.suffix == ".js": ctype = "application/javascript"
                elif fp.suffix == ".css": ctype = "text/css"
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                for k,v in cors_headers.items(): self.send_header(k,v)
                self.end_headers()
                self.wfile.write(fp.read_bytes())
                return

        self.send_response(404)
        self.send_header("Content-Type", "application/json")
        for k,v in cors_headers.items(): self.send_header(k,v)
        self.end_headers()
        self.wfile.write(json.dumps({"error":"not found","path":path}).encode())

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

def find_free_port(start=9090, max_try=20):
    import socket
    for p in range(start, start+max_try):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("0.0.0.0", p))
                return p
            except OSError:
                continue
    return start

def main():
    import argparse
    parser = argparse.ArgumentParser(description="System Monitor Dashboard")
    parser.add_argument("--port", type=int, default=None, help="Port to listen (default auto 9090)")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind")
    parser.add_argument("--projects-root", default=None, help="Root directory to scan for projects (default: $PROJECTS_ROOT or $HOME)")
    args = parser.parse_args()
    if args.projects_root:
        global HOME_DIR
        HOME_DIR = Path(args.projects_root)
    port = args.port or int(os.environ.get("PORT") or 0) or find_free_port(9090)
    # If env PORT=0, auto find
    if port == 0:
        port = find_free_port(9090)
    # Check if port in use, find free
    import socket
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind((args.host, port))
    except OSError:
        old = port
        port = find_free_port(port+1)
        print(f"Port {old} in use, using {port} instead")

    ThreadingHTTPServer.allow_reuse_address = True
    server = ThreadingHTTPServer((args.host, port), MonitorHandler)
    # Warm up collectors in background (non-blocking) to avoid delaying bind
    def warmup():
        try:
            # light warmup: only cpu/net which need delta, skip heavy projects/docker
            get_cpu_stats()
            get_network()
            get_disk_io()
            time.sleep(0.5)
            get_cpu_stats()
            get_network()
            get_disk_io()
        except Exception as e:
            print(f"Warmup error: {e}")
    threading.Thread(target=warmup, daemon=True).start()
    print(f"System Monitor running at http://{args.host}:{port}")
    print(f"  Dashboard: http://localhost:{port}/")
    print(f"  API: http://localhost:{port}/api/stats")
    print(f"  Stream: http://localhost:{port}/api/stream")
    print(f"  Press Ctrl+C to stop")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
        server.shutdown()

if __name__ == "__main__":
    main()
