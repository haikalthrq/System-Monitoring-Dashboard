#!/usr/bin/env python3
"""
System Monitor - Realtime Dashboard
Universal Cross-Platform: Linux & Windows
Pure stdlib, no pip required. Python 3.8+
Monitors: CPU, RAM, Disk, Network, Processes, Docker, Projects, Uptime
"""
import sys
import os
import re
import json
import time
import math
import shutil
import string
import platform
import subprocess
import threading
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"
IS_LINUX = sys.platform.startswith("linux")

ROOT = Path(__file__).parent.resolve()
STATIC_DIR = ROOT / "static"

def get_default_projects_root():
    env_root = os.environ.get("PROJECTS_ROOT")
    if env_root and Path(env_root).exists():
        return Path(env_root)
    try:
        parent = ROOT.parent
        if parent.exists() and parent != parent.parent:
            other_dirs = [p for p in parent.iterdir() if p.is_dir() and p.resolve() != ROOT and not p.name.startswith(".")]
            if other_dirs:
                return parent
    except Exception:
        pass
    return Path.home()

HOME_DIR = get_default_projects_root()

# Global state for delta calculations and caching
_state = {
    "cpu_prev": None,
    "cpu_prev_per_core": {},
    "win_cpu_prev_cores": None,
    "net_prev": {},
    "net_prev_win": {},
    "disk_io_prev": {},
    "last_collect": 0,
    "cached": None,
    "win_loadavg": [0.0, 0.0, 0.0],
    "win_procs": {"by_cpu": [], "by_mem": [], "total": 0},
    "win_procs_time": 0,
    "win_procs_updating": False,
    "projects_cache": None,
    "projects_cache_time": 0,
    "projects_refreshing": False,
    "docker_cache": None,
    "docker_cache_time": 0,
}

# --- WINDOWS CTYPES DEFINITIONS ---
if IS_WINDOWS:
    import ctypes
    import winreg

    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong),
            ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    class SYSTEM_PROCESSOR_PERFORMANCE_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("IdleTime", ctypes.c_int64),
            ("KernelTime", ctypes.c_int64),
            ("UserTime", ctypes.c_int64),
            ("DpcTime", ctypes.c_int64),
            ("InterruptTime", ctypes.c_int64),
            ("InterruptCount", ctypes.c_uint32),
        ]

    class MIB_IFROW(ctypes.Structure):
        _fields_ = [
            ("wszName", ctypes.c_wchar * 256),
            ("dwIndex", ctypes.c_ulong),
            ("dwType", ctypes.c_ulong),
            ("dwMtu", ctypes.c_ulong),
            ("dwSpeed", ctypes.c_ulong),
            ("dwPhysAddrLen", ctypes.c_ulong),
            ("bPhysAddr", ctypes.c_ubyte * 8),
            ("dwAdminStatus", ctypes.c_ulong),
            ("dwOperStatus", ctypes.c_ulong),
            ("dwLastChange", ctypes.c_ulong),
            ("dwInOctets", ctypes.c_ulong),
            ("dwInUcastPkts", ctypes.c_ulong),
            ("dwInNUcastPkts", ctypes.c_ulong),
            ("dwInDiscards", ctypes.c_ulong),
            ("dwInErrors", ctypes.c_ulong),
            ("dwInUnknownProtos", ctypes.c_ulong),
            ("dwOutOctets", ctypes.c_ulong),
            ("dwOutUcastPkts", ctypes.c_ulong),
            ("dwOutNUcastPkts", ctypes.c_ulong),
            ("dwOutDiscards", ctypes.c_ulong),
            ("dwOutErrors", ctypes.c_ulong),
            ("dwOutQLen", ctypes.c_ulong),
            ("dwDescrLen", ctypes.c_ulong),
            ("bDescr", ctypes.c_char * 256),
        ]

def read_file(path, default=""):
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()
    except:
        return default

# ==============================================================================
# CPU STATS
# ==============================================================================
def get_cpu_stats():
    if IS_WINDOWS:
        return _get_cpu_stats_windows()
    return _get_cpu_stats_linux()

def _get_cpu_stats_linux():
    try:
        line = read_file("/proc/stat").splitlines()[0]
        parts = line.split()
        vals = list(map(int, parts[1:8]))
        total = sum(vals)
        idle = vals[3] + vals[4]
        prev = _state["cpu_prev"]
        if prev is None:
            cpu_percent = 0.0
        else:
            prev_total, prev_idle = prev
            diff_total = total - prev_total
            diff_idle = idle - prev_idle
            cpu_percent = (100 * (1 - diff_idle / diff_total)) if diff_total > 0 else 0.0
        _state["cpu_prev"] = (total, idle)

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
                    pct = 0.0
                else:
                    pt, pi = prev_c
                    dt = c_total - pt
                    di = c_idle - pi
                    pct = (100 * (1 - di / dt)) if dt > 0 else 0.0
                _state["cpu_prev_per_core"][core_id] = (c_total, c_idle)
                per_core.append(round(max(0.0, min(100.0, pct)), 1))
                per_core_detail.append({"core": core_id, "usage": round(max(0.0, min(100.0, pct)), 1)})

        cpuinfo = read_file("/proc/cpuinfo")
        model = "Unknown"
        for l in cpuinfo.splitlines():
            if "model name" in l:
                model = l.split(":", 1)[1].strip()
                break
        cores = cpuinfo.count("processor")
        mhz = "0"
        for l in cpuinfo.splitlines():
            if "cpu MHz" in l:
                mhz = l.split(":", 1)[1].strip()
                break

        loadavg = read_file("/proc/loadavg").strip().split()[:3]
        loadavg = [float(x) for x in loadavg] if len(loadavg) == 3 else [0.0, 0.0, 0.0]

        return {
            "usage": round(max(0.0, min(100.0, cpu_percent)), 1),
            "per_core": per_core,
            "per_core_detail": per_core_detail,
            "cores": cores if cores else len(per_core) if per_core else 1,
            "model": model,
            "mhz": mhz,
            "loadavg": loadavg,
        }
    except Exception as e:
        return {"usage": 0, "per_core": [], "per_core_detail": [], "cores": 0, "model": str(e), "mhz": "0", "loadavg": [0,0,0]}

def _get_cpu_stats_windows():
    try:
        cores = os.cpu_count() or 1
        buf_type = SYSTEM_PROCESSOR_PERFORMANCE_INFORMATION * cores
        buf = buf_type()
        ctypes.windll.ntdll.NtQuerySystemInformation(8, ctypes.byref(buf), ctypes.sizeof(buf), None)

        now_cores = [(buf[i].IdleTime, buf[i].KernelTime, buf[i].UserTime) for i in range(cores)]
        prev_cores = _state.get("win_cpu_prev_cores")

        per_core = []
        per_core_detail = []
        total_pct = 0.0

        if prev_cores is None or len(prev_cores) != cores:
            per_core = [0.0] * cores
            per_core_detail = [{"core": f"cpu{i}", "usage": 0.0} for i in range(cores)]
        else:
            for i in range(cores):
                i1, k1, u1 = prev_cores[i]
                i2, k2, u2 = now_cores[i]
                d_idle = i2 - i1
                d_kernel = k2 - k1
                d_user = u2 - u1
                d_total = d_kernel + d_user
                pct = (1.0 - (d_idle / d_total)) * 100.0 if d_total > 0 else 0.0
                pct = round(max(0.0, min(100.0, pct)), 1)
                per_core.append(pct)
                per_core_detail.append({"core": f"cpu{i}", "usage": pct})
            total_pct = sum(per_core) / cores if cores else 0.0

        _state["win_cpu_prev_cores"] = now_cores

        model = platform.processor() or "Windows Processor"
        mhz = "0"
        try:
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0")
            model_val, _ = winreg.QueryValueEx(key, "ProcessorNameString")
            mhz_val, _ = winreg.QueryValueEx(key, "~MHz")
            model = model_val.strip()
            mhz = str(mhz_val)
        except Exception:
            pass

        # Simulated Unix Load Average for Windows via EMA
        inst_load = (total_pct / 100.0) * cores
        l1, l5, l15 = _state.get("win_loadavg", [0.0, 0.0, 0.0])
        a1 = 1.0 - math.exp(-1.0 / 60.0)
        a5 = 1.0 - math.exp(-1.0 / 300.0)
        a15 = 1.0 - math.exp(-1.0 / 900.0)
        l1 = l1 + a1 * (inst_load - l1)
        l5 = l5 + a5 * (inst_load - l5)
        l15 = l15 + a15 * (inst_load - l15)
        _state["win_loadavg"] = [round(l1, 2), round(l5, 2), round(l15, 2)]

        return {
            "usage": round(max(0.0, min(100.0, total_pct)), 1),
            "per_core": per_core,
            "per_core_detail": per_core_detail,
            "cores": cores,
            "model": model,
            "mhz": mhz,
            "loadavg": _state["win_loadavg"],
        }
    except Exception as e:
        return {"usage": 0, "per_core": [], "per_core_detail": [], "cores": os.cpu_count() or 1, "model": str(e), "mhz": "0", "loadavg": [0,0,0]}

# ==============================================================================
# MEMORY
# ==============================================================================
def get_memory():
    if IS_WINDOWS:
        return _get_memory_windows()
    return _get_memory_linux()

def _get_memory_linux():
    try:
        mem = {}
        for l in read_file("/proc/meminfo").splitlines():
            if ":" in l:
                k, v = l.split(":", 1)
                num = re.findall(r"\d+", v)
                if num:
                    mem[k.strip()] = int(num[0]) * 1024
        total = mem.get("MemTotal", 0)
        free = mem.get("MemFree", 0)
        available = mem.get("MemAvailable", 0)
        buffers = mem.get("Buffers", 0)
        cached = mem.get("Cached", 0)
        swap_total = mem.get("SwapTotal", 0)
        swap_free = mem.get("SwapFree", 0)
        used = total - available if available else (total - free - buffers - cached if total else 0)
        percent = (used / total * 100) if total else 0
        swap_used = swap_total - swap_free
        return {
            "total": total,
            "free": free,
            "available": available,
            "used": used,
            "buffers": buffers,
            "cached": cached,
            "swap_total": swap_total,
            "swap_free": swap_free,
            "swap_used": swap_used,
            "percent": round(percent, 1),
            "swap_percent": round(swap_used / swap_total * 100, 1) if swap_total else 0,
        }
    except Exception as e:
        return {"total":0,"free":0,"available":0,"used":0,"buffers":0,"cached":0,"swap_total":0,"swap_free":0,"swap_used":0,"percent":0,"swap_percent":0,"error":str(e)}

def _get_memory_windows():
    try:
        stat = MEMORYSTATUSEX()
        stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))

        total = stat.ullTotalPhys
        avail = stat.ullAvailPhys
        used = total - avail
        percent = float(stat.dwMemoryLoad)

        swap_total = stat.ullTotalPageFile
        swap_free = stat.ullAvailPageFile
        swap_used = max(0, swap_total - swap_free)
        swap_pct = (swap_used / swap_total * 100.0) if swap_total else 0.0

        return {
            "total": total,
            "free": avail,
            "available": avail,
            "used": used,
            "buffers": 0,
            "cached": 0,
            "swap_total": swap_total,
            "swap_free": swap_free,
            "swap_used": swap_used,
            "percent": round(percent, 1),
            "swap_percent": round(swap_pct, 1),
        }
    except Exception as e:
        return {"total":0,"free":0,"available":0,"used":0,"buffers":0,"cached":0,"swap_total":0,"swap_free":0,"swap_used":0,"percent":0,"swap_percent":0,"error":str(e)}

# ==============================================================================
# DISKS
# ==============================================================================
def get_disks():
    if IS_WINDOWS:
        return _get_disks_windows()
    return _get_disks_linux()

def _get_disks_linux():
    disks = []
    try:
        exclude_fs = {"tmpfs","devtmpfs","efivarfs","squashfs","overlay","cgroup","cgroup2","proc","sysfs","devpts","securityfs","pstore","bpf"}
        exclude_mount = {"/dev/shm","/run/lock","/sys/firmware/efi/efivars"}
        seen = set()
        for line in read_file("/proc/mounts").splitlines():
            parts = line.split()
            if len(parts) < 3: continue
            dev, mnt, fstype = parts[0], parts[1], parts[2]
            if fstype in exclude_fs and mnt not in ["/","/mnt","/boot","/boot/efi"]: continue
            if mnt in exclude_mount or mnt in seen: continue
            seen.add(mnt)
            try:
                usage = shutil.disk_usage(mnt)
                st = os.statvfs(mnt)
                total_inodes = st.f_files
                free_inodes = st.f_ffree
                used_inodes = total_inodes - free_inodes
                inode_percent = (used_inodes / total_inodes * 100) if total_inodes else 0
                percent = (usage.used / usage.total * 100) if usage.total else 0
                disks.append({
                    "device": dev,
                    "mount": mnt,
                    "fstype": fstype,
                    "total": usage.total,
                    "used": usage.used,
                    "free": usage.free,
                    "percent": round(percent, 1),
                    "inodes_total": total_inodes,
                    "inodes_used": used_inodes,
                    "inodes_free": free_inodes,
                    "inodes_percent": round(inode_percent, 1),
                })
            except:
                continue
        disks.sort(key=lambda x: (0 if x["mount"] == "/" else 1, x["mount"]))
        return disks
    except Exception as e:
        return [{"error": str(e)}]

def _get_disks_windows():
    disks = []
    try:
        for letter in string.ascii_uppercase:
            p = f"{letter}:\\"
            if os.path.exists(p):
                try:
                    usage = shutil.disk_usage(p)
                    percent = (usage.used / usage.total * 100.0) if usage.total else 0.0
                    disks.append({
                        "device": f"{letter}:",
                        "mount": p,
                        "fstype": "NTFS",
                        "total": usage.total,
                        "used": usage.used,
                        "free": usage.free,
                        "percent": round(percent, 1),
                        "inodes_total": 0,
                        "inodes_used": 0,
                        "inodes_free": 0,
                        "inodes_percent": 0.0,
                    })
                except:
                    continue
        disks.sort(key=lambda x: (0 if x["device"].startswith("C") else 1, x["device"]))
        return disks
    except Exception as e:
        return [{"error": str(e)}]

# ==============================================================================
# DISK I/O
# ==============================================================================
def get_disk_io():
    if IS_WINDOWS:
        return _get_disk_io_windows()
    return _get_disk_io_linux()

def _get_disk_io_linux():
    try:
        current = {}
        for line in read_file("/proc/diskstats").splitlines():
            parts = line.split()
            if len(parts) < 14: continue
            dev = parts[2]
            if dev.startswith("loop") or dev.startswith("ram"): continue
            reads = int(parts[5])
            writes = int(parts[9])
            current[dev] = (reads * 512, writes * 512)
        now = time.time()
        prev = _state.get("disk_io_prev", {})
        prev_time = _state.get("disk_io_time", now)
        delta_t = max(0.1, now - prev_time)
        result = []
        for dev, (rb, wb) in current.items():
            if dev in prev:
                prb, pwb = prev[dev]
                result.append({
                    "device": dev,
                    "read_bytes": rb,
                    "write_bytes": wb,
                    "read_rate": max(0.0, (rb - prb) / delta_t),
                    "write_rate": max(0.0, (wb - pwb) / delta_t)
                })
            else:
                result.append({"device": dev, "read_bytes": rb, "write_bytes": wb, "read_rate": 0.0, "write_rate": 0.0})
        _state["disk_io_prev"] = current
        _state["disk_io_time"] = now
        result.sort(key=lambda x: x["device"])
        return result
    except Exception as e:
        return [{"error": str(e)}]

def _get_disk_io_windows():
    result = []
    try:
        for letter in string.ascii_uppercase:
            p = f"{letter}:\\"
            if os.path.exists(p):
                result.append({
                    "device": f"{letter}:",
                    "read_bytes": 0,
                    "write_bytes": 0,
                    "read_rate": 0.0,
                    "write_rate": 0.0
                })
    except:
        pass
    return result

# ==============================================================================
# NETWORK
# ==============================================================================
def get_network():
    if IS_WINDOWS:
        return _get_network_windows()
    return _get_network_linux()

def _get_network_linux():
    try:
        current = {}
        for line in read_file("/proc/net/dev").splitlines():
            if ":" not in line or "|" in line: continue
            iface, data = line.split(":", 1)
            iface = iface.strip()
            fields = data.split()
            if len(fields) < 16: continue
            current[iface] = (int(fields[0]), int(fields[8]), int(fields[1]), int(fields[9]))
        now = time.time()
        prev = _state.get("net_prev", {})
        prev_time = _state.get("net_time", now)
        delta_t = max(0.1, now - prev_time)
        result = []
        for iface, (rx, tx, rxp, txp) in current.items():
            if iface in prev:
                prx, ptx, prxp, ptxp = prev[iface]
                result.append({
                    "iface": iface,
                    "rx_bytes": rx,
                    "tx_bytes": tx,
                    "rx_rate": max(0.0, (rx - prx) / delta_t),
                    "tx_rate": max(0.0, (tx - ptx) / delta_t),
                    "rx_packets": rxp,
                    "tx_packets": txp,
                    "rx_p_rate": max(0.0, (rxp - prxp) / delta_t),
                    "tx_p_rate": max(0.0, (txp - ptxp) / delta_t),
                })
            else:
                result.append({"iface": iface, "rx_bytes": rx, "tx_bytes": tx, "rx_rate": 0.0, "tx_rate": 0.0, "rx_packets": rxp, "tx_packets": txp, "rx_p_rate": 0.0, "tx_p_rate": 0.0})
        _state["net_prev"] = current
        _state["net_time"] = now
        result.sort(key=lambda x: (0 if x["iface"] == "eth0" else 1 if x["iface"] == "ens3" else 2, x["iface"]))
        return result
    except Exception as e:
        return [{"error": str(e)}]

def _get_network_windows():
    current = {}
    try:
        size = ctypes.c_ulong(0)
        ctypes.windll.iphlpapi.GetIfTable(None, ctypes.byref(size), False)
        buf = ctypes.create_string_buffer(size.value)
        ctypes.windll.iphlpapi.GetIfTable(buf, ctypes.byref(size), False)
        num_entries = ctypes.c_ulong.from_buffer_copy(buf[:4]).value
        offset = 4
        row_size = ctypes.sizeof(MIB_IFROW)
        seen = set()
        for _ in range(num_entries):
            row = MIB_IFROW.from_buffer_copy(buf[offset:offset+row_size])
            offset += row_size
            desc = row.bDescr[:row.dwDescrLen].decode("latin1", "ignore").strip()
            if any(sub in desc for sub in ["-WFP", "-QoS", "Virtual Adapter #", "Teredo", "Loopback"]):
                continue
            if desc in seen: continue
            seen.add(desc)
            if row.dwInOctets > 0 or row.dwOutOctets > 0:
                short_name = desc.split("#")[0].strip()
                current[short_name] = (int(row.dwInOctets), int(row.dwOutOctets), int(row.dwInUcastPkts), int(row.dwOutUcastPkts))
    except Exception:
        pass

    now = time.time()
    prev = _state.get("net_prev_win", {})
    prev_time = _state.get("net_time_win", now)
    delta_t = max(0.1, now - prev_time)
    result = []

    for iface, (rx, tx, rxp, txp) in current.items():
        if iface in prev:
            prx, ptx, prxp, ptxp = prev[iface]
            drx = rx - prx if rx >= prx else rx
            dtx = tx - ptx if tx >= ptx else tx
            drxp = rxp - prxp if rxp >= prxp else rxp
            dtxp = txp - ptxp if txp >= ptxp else txp
            result.append({
                "iface": iface,
                "rx_bytes": rx,
                "tx_bytes": tx,
                "rx_rate": max(0.0, drx / delta_t),
                "tx_rate": max(0.0, dtx / delta_t),
                "rx_packets": rxp,
                "tx_packets": txp,
                "rx_p_rate": max(0.0, drxp / delta_t),
                "tx_p_rate": max(0.0, dtxp / delta_t),
            })
        else:
            result.append({"iface": iface, "rx_bytes": rx, "tx_bytes": tx, "rx_rate": 0.0, "tx_rate": 0.0, "rx_packets": rxp, "tx_packets": txp, "rx_p_rate": 0.0, "tx_p_rate": 0.0})

    _state["net_prev_win"] = current
    _state["net_time_win"] = now
    result.sort(key=lambda x: (x["rx_bytes"] + x["tx_bytes"]), reverse=True)
    return result if result else [{"iface": "Ethernet", "rx_bytes": 0, "tx_bytes": 0, "rx_rate": 0.0, "tx_rate": 0.0, "rx_packets": 0, "tx_packets": 0, "rx_p_rate": 0.0, "tx_p_rate": 0.0}]

# ==============================================================================
# UPTIME & HOST INFO
# ==============================================================================
def human_duration(secs):
    secs = int(secs)
    days = secs // 86400
    hours = (secs % 86400) // 3600
    mins = (secs % 3600) // 60
    if days: return f"{days}d {hours}h {mins}m"
    if hours: return f"{hours}h {mins}m"
    return f"{mins}m {secs%60}s"

def get_uptime():
    if IS_WINDOWS:
        return _get_uptime_windows()
    return _get_uptime_linux()

def _get_uptime_linux():
    try:
        up = read_file("/proc/uptime").split()
        secs = float(up[0]) if up else 0.0
        users = 0
        try:
            out = subprocess.check_output(["who"], text=True, timeout=1)
            users = len(out.strip().splitlines()) if out.strip() else 0
        except:
            pass
        hostname = read_file("/proc/sys/kernel/hostname").strip() or os.uname().nodename
        os_pretty = read_file("/etc/os-release")
        pretty = "Linux"
        for l in os_pretty.splitlines():
            if l.startswith("PRETTY_NAME="):
                pretty = l.split("=", 1)[1].strip().strip('"')
                break
        kernel = os.uname().release
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
        return {"uptime_seconds":0,"uptime_human":"0s","users":0,"hostname":"unknown","os":str(e),"kernel":"","loadavg":[0,0,0]}

def _get_uptime_windows():
    try:
        secs = ctypes.windll.kernel32.GetTickCount64() / 1000.0
        hostname = platform.node() or os.environ.get("COMPUTERNAME", "Windows-PC")
        pretty = f"{platform.system()} {platform.release()}"
        kernel = platform.version()
        return {
            "uptime_seconds": secs,
            "uptime_human": human_duration(secs),
            "users": 1,
            "hostname": hostname,
            "os": pretty,
            "kernel": kernel,
            "loadavg": _state.get("win_loadavg", [0.0, 0.0, 0.0]),
        }
    except Exception as e:
        return {"uptime_seconds":0,"uptime_human":"0s","users":0,"hostname":"unknown","os":str(e),"kernel":"","loadavg":[0,0,0]}

# ==============================================================================
# PROCESSES
# ==============================================================================
def count_processes():
    if IS_WINDOWS:
        try:
            pids = (ctypes.c_ulong * 4096)()
            cb_needed = ctypes.c_ulong()
            ctypes.windll.kernel32.K32EnumProcesses(ctypes.byref(pids), ctypes.sizeof(pids), ctypes.byref(cb_needed))
            return cb_needed.value // ctypes.sizeof(ctypes.c_ulong)
        except:
            return 0
    try:
        import glob
        return len(glob.glob("/proc/[0-9]*"))
    except:
        return 0

def _update_win_procs_background(limit=15):
    try:
        cmd_cpu = ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                   f"Get-Process | Sort-Object CPU -Descending | Select-Object -First {limit} Id,ProcessName,CPU,WorkingSet64 | ConvertTo-Json"]
        cmd_mem = ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                   f"Get-Process | Sort-Object WorkingSet64 -Descending | Select-Object -First {limit} Id,ProcessName,CPU,WorkingSet64 | ConvertTo-Json"]

        out_cpu = subprocess.check_output(cmd_cpu, text=True, timeout=3)
        out_mem = subprocess.check_output(cmd_mem, text=True, timeout=3)

        raw_cpu = json.loads(out_cpu) if out_cpu.strip() else []
        raw_mem = json.loads(out_mem) if out_mem.strip() else []

        if isinstance(raw_cpu, dict): raw_cpu = [raw_cpu]
        if isinstance(raw_mem, dict): raw_mem = [raw_mem]

        total_ram = _get_memory_windows()["total"] or 1

        def normalize(p):
            pid = p.get("Id", 0)
            name = p.get("ProcessName", "unknown")
            cpu_val = p.get("CPU")
            cpu_pct = float(cpu_val) if cpu_val is not None else 0.0
            ws = p.get("WorkingSet64", 0)
            mem_pct = round((ws / total_ram * 100.0), 1) if total_ram else 0.0
            return {
                "pid": pid,
                "ppid": 0,
                "name": name,
                "cpu": round(cpu_pct, 1),
                "mem": mem_pct,
                "etime": "-",
                "stat": "R",
                "cmd": name,
            }

        by_cpu = [normalize(p) for p in raw_cpu]
        by_mem = [normalize(p) for p in raw_mem]

        _state["win_procs"] = {
            "by_cpu": by_cpu,
            "by_mem": by_mem,
            "total": count_processes()
        }
        _state["win_procs_time"] = time.time()
    except Exception:
        pass
    finally:
        _state["win_procs_updating"] = False

def get_processes(limit=10, sort_by="cpu"):
    if IS_WINDOWS:
        now = time.time()
        if now - _state.get("win_procs_time", 0) > 3.0:
            if not _state.get("win_procs_updating"):
                _state["win_procs_updating"] = True
                threading.Thread(target=_update_win_procs_background, args=(limit,), daemon=True).start()
        cached = _state.get("win_procs", {"by_cpu": [], "by_mem": [], "total": 0})
        if not cached.get("by_cpu"):
            cached["total"] = count_processes()
        return cached

    procs = []
    try:
        out = subprocess.check_output(
            ["ps", "-eo", "pid,ppid,comm,pcpu,pmem,etime,stat,cmd", "--sort=-%cpu"],
            text=True, timeout=2
        )
        for l in out.strip().splitlines()[1:limit+1]:
            parts = l.strip().split(None, 7)
            if len(parts) < 7: continue
            pid, ppid, comm, pcpu, pmem, etime, stat = parts[:7]
            cmd = parts[7] if len(parts) > 7 else comm
            if len(cmd) > 80: cmd = cmd[:80] + "..."
            procs.append({
                "pid": int(pid), "ppid": int(ppid), "name": comm,
                "cpu": float(pcpu), "mem": float(pmem), "etime": etime, "stat": stat, "cmd": cmd
            })
        try:
            out_mem = subprocess.check_output(
                ["ps", "-eo", "pid,ppid,comm,pcpu,pmem,etime,stat,cmd", "--sort=-%mem"],
                text=True, timeout=2
            )
            procs_mem = []
            for l in out_mem.strip().splitlines()[1:limit+1]:
                parts = l.strip().split(None, 7)
                if len(parts) < 7: continue
                pid, ppid, comm, pcpu, pmem, etime, stat = parts[:7]
                cmd = parts[7] if len(parts) > 7 else comm
                if len(cmd) > 80: cmd = cmd[:80] + "..."
                procs_mem.append({
                    "pid": int(pid), "ppid": int(ppid), "name": comm,
                    "cpu": float(pcpu), "mem": float(pmem), "etime": etime, "stat": stat, "cmd": cmd
                })
        except Exception:
            procs_mem = sorted(procs, key=lambda x: x["mem"], reverse=True)[:limit]
        return {"by_cpu": procs, "by_mem": procs_mem, "total": count_processes()}
    except Exception as e:
        return {"by_cpu": [], "by_mem": [], "total": count_processes(), "error": str(e)}

# ==============================================================================
# DOCKER
# ==============================================================================
def get_docker():
    containers = []
    try:
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
                containers.append({
                    "id": j.get("ID", "")[:12],
                    "image": j.get("Image", ""),
                    "name": j.get("Names", ""),
                    "status": j.get("Status", ""),
                    "state": j.get("State", ""),
                    "ports": j.get("Ports", ""),
                    "created": j.get("CreatedAt", ""),
                })
            except:
                continue
        if containers:
            try:
                stats_out = subprocess.check_output(
                    ["docker", "stats", "--no-stream", "--format", "{{json .}}"],
                    text=True, timeout=3
                )
                stats_map = {}
                for line in stats_out.strip().splitlines():
                    try:
                        j = json.loads(line)
                        stats_map[j.get("Name", "")] = j
                    except:
                        continue
                for c in containers:
                    s = stats_map.get(c["name"])
                    if s:
                        c["cpu"] = s.get("CPUPerc", "-")
                        c["mem_usage"] = s.get("MemUsage", "-")
                        c["mem_perc"] = s.get("MemPerc", "-")
                        c["net_io"] = s.get("NetIO", "-")
                        c["block_io"] = s.get("BlockIO", "-")
            except Exception:
                pass
        return {
            "available": True,
            "containers": containers,
            "count": len(containers),
            "running": len([c for c in containers if "Up" in c.get("status", "")])
        }
    except Exception as e:
        return {"available": False, "containers": [], "error": str(e)}

def get_docker_cached():
    now = time.time()
    if _state.get("docker_cache") is None or now - _state.get("docker_cache_time", 0) > 5:
        d = get_docker()
        _state["docker_cache"] = d
        _state["docker_cache_time"] = now
        return d
    return _state["docker_cache"]

# ==============================================================================
# PROJECTS
# ==============================================================================
def human_bytes(b):
    if b is None: return "-"
    for unit in ['B','KB','MB','GB','TB']:
        if abs(b) < 1024.0:
            return f"{b:.1f} {unit}" if unit != 'B' else f"{b} B"
        b /= 1024.0
    return f"{b:.1f} PB"

def fast_dir_size(path: Path, max_files=5000):
    total = 0
    count = 0
    skip_dirs = {'.git', 'node_modules', 'venv', '.venv', '__pycache__', '.next', 'bin', 'obj', 'target'}
    try:
        for root, dirs, files in os.walk(path):
            dirs[:] = [d for d in dirs if d not in skip_dirs and not d.startswith('.')]
            for f in files:
                try:
                    fp = os.path.join(root, f)
                    total += os.path.getsize(fp)
                    count += 1
                    if count >= max_files:
                        return total
                except:
                    pass
    except:
        pass
    return total

def detect_project_type(path: Path):
    types = []
    def has_file(fname):
        if (path / fname).exists(): return True
        try:
            for sub in path.iterdir():
                if sub.is_dir() and not sub.name.startswith(".") and sub.name not in {"node_modules", "venv", ".venv", "bin", "obj"}:
                    if (sub / fname).exists(): return True
        except:
            pass
        return False

    if (path / "docker-compose.yml").exists() or (path / "compose.yml").exists() or (path / "infra" / "docker-compose.yml").exists() or has_file("docker-compose.yml"):
        types.append("docker")
    if (path / "Dockerfile").exists() or has_file("Dockerfile"):
        if "docker" not in types: types.append("docker")
    if (path / "package.json").exists() or has_file("package.json"):
        types.append("node")
    if (path / "requirements.txt").exists() or (path / "pyproject.toml").exists() or (path / "uv.lock").exists() or (path / "bot.py").exists() or has_file("requirements.txt") or has_file("pyproject.toml"):
        types.append("python")
    try:
        if list(path.glob("*.csproj")) or any(sub.is_dir() and list(sub.glob("*.csproj")) for sub in path.iterdir() if not sub.name.startswith(".")):
            types.append("dotnet")
    except:
        pass
    if (path / "server.jar").exists() or (path / "server.properties").exists():
        types.append("minecraft")
    if (path / "start-server.sh").exists() or (path / "valheim.service").exists() or (path / "server" / "valheim_server.x86_64").exists():
        types.append("valheim")
    if (path / ".git").exists():
        types.append("git")
    if not types:
        if any((path / f).exists() for f in ["apps", "src", "services", "infra"]):
            types.append("monorepo")
        else:
            types.append("generic")
    return types

def _get_projects_sync(docker_info=None):
    projects = []
    try:
        home = HOME_DIR
        candidates = []
        skip_home_names = {"snap", "tmp", "AppData", "Application Data", "Cookies", "Local Settings", "My Documents", "NetHood", "PrintHood", "Recent", "SendTo", "Start Menu", "Templates"}
        for p in home.iterdir():
            if p.is_dir() and not p.name.startswith(".") and p.name not in skip_home_names:
                candidates.append(p)

        for path in sorted(candidates, key=lambda x: x.name.lower()):
            try:
                size_bytes = 0
                if IS_LINUX and shutil.which("du"):
                    try:
                        out = subprocess.check_output(["du", "-sb", str(path)], text=True, timeout=2)
                        size_bytes = int(out.split()[0])
                    except:
                        size_bytes = fast_dir_size(path)
                else:
                    size_bytes = fast_dir_size(path)
                size_human = human_bytes(size_bytes)

                git_branch = ""
                git_status = ""
                if (path / ".git").exists() and shutil.which("git"):
                    try:
                        branch = subprocess.check_output(["git", "-C", str(path), "branch", "--show-current"], text=True, timeout=1).strip()
                        git_branch = branch
                        status_out = subprocess.check_output(["git", "-C", str(path), "status", "--porcelain"], text=True, timeout=1)
                        dirty = len(status_out.strip().splitlines()) if status_out.strip() else 0
                        git_status = f"{dirty} changes" if dirty else "clean"
                    except:
                        pass

                services = [svc.name for svc in path.glob("*.service")]
                service_active = {}
                if IS_LINUX and shutil.which("systemctl"):
                    for svc in services:
                        try:
                            out = subprocess.check_output(["systemctl", "is-active", svc], text=True, timeout=1).strip()
                            service_active[svc] = out
                        except:
                            service_active[svc] = "unknown"

                types = detect_project_type(path)
                try:
                    mtime = path.stat().st_mtime
                    last_mod = time.strftime("%Y-%m-%d %H:%M", time.localtime(mtime))
                except:
                    last_mod = ""

                running = False
                di = docker_info if docker_info is not None else {}
                for c in di.get("containers", []):
                    clean_pname = path.name.lower().replace("-", "")
                    clean_cname = c.get("name", "").lower().replace("-", "")
                    if clean_pname in clean_cname or path.name.lower() in c.get("name", "").lower():
                        if "Up" in c.get("status", "") or c.get("state") == "running":
                            running = True
                            break

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
                projects.append({"name": path.name, "path": str(path), "types": ["error"], "error": str(e)})

        projects.sort(key=lambda x: (0 if x.get("running") else 1, x["name"].lower()))
        return projects
    except Exception as e:
        return [{"name": "error", "error": str(e)}]

def get_projects():
    now = time.time()
    cache = _state.get("projects_cache")
    cache_time = _state.get("projects_cache_time", 0)
    if cache is None or now - cache_time > 30:
        docker_info = _state.get("docker_cache")
        if not _state.get("projects_refreshing"):
            _state["projects_refreshing"] = True
            def do_refresh():
                try:
                    p = _get_projects_sync(docker_info=docker_info)
                    _state["projects_cache"] = p
                    _state["projects_cache_time"] = time.time()
                finally:
                    _state["projects_refreshing"] = False
            threading.Thread(target=do_refresh, daemon=True).start()
        if cache is not None:
            return cache
        try:
            return [{"name": p.name, "path": str(p), "types": detect_project_type(p), "size_human": "...", "git_branch": "", "git_status": "loading...", "services": [], "service_active": {}, "last_modified": "", "running": False}
                    for p in sorted([x for x in HOME_DIR.iterdir() if x.is_dir() and not x.name.startswith(".")], key=lambda x: x.name.lower())]
        except Exception:
            return []
    return cache

# ==============================================================================
# AGGREGATE
# ==============================================================================
def collect_all():
    cpu = get_cpu_stats()
    mem = get_memory()
    disks = get_disks()
    disk_io = get_disk_io()
    net = get_network()
    uptime = get_uptime()
    procs = get_processes(limit=8)
    docker = get_docker_cached()
    projects = get_projects()
    now = time.time()

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

# ==============================================================================
# HTTP HANDLER
# ==============================================================================
class MonitorHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        return

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        cors_headers = {
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type",
        }

        if path in ("/api/stats", "/api/metrics"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            for k, v in cors_headers.items(): self.send_header(k, v)
            self.end_headers()
            data = collect_all()
            self.wfile.write(json.dumps(data).encode())
            return

        if path in ("/api/stream", "/api/sse"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("X-Accel-Buffering", "no")
            for k, v in cors_headers.items(): self.send_header(k, v)
            self.end_headers()
            try:
                interval = float(qs.get("interval", ["1"])[0])
                if interval < 0.5: interval = 0.5
                if interval > 10: interval = 10
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
            for k, v in cors_headers.items(): self.send_header(k, v)
            self.end_headers()
            if qs.get("refresh", ["0"])[0] == "1":
                projects = _get_projects_sync(docker_info=get_docker_cached())
            else:
                projects = get_projects()
            self.wfile.write(json.dumps({"projects": projects}).encode())
            return

        if path == "/api/docker":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            for k, v in cors_headers.items(): self.send_header(k, v)
            self.end_headers()
            self.wfile.write(json.dumps(get_docker_cached()).encode())
            return

        if path == "/api/processes":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            for k, v in cors_headers.items(): self.send_header(k, v)
            self.end_headers()
            sort = qs.get("sort", ["cpu"])[0]
            self.wfile.write(json.dumps(get_processes(limit=15, sort_by=sort)).encode())
            return

        if path == "/api/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            for k, v in cors_headers.items(): self.send_header(k, v)
            self.end_headers()
            self.wfile.write(json.dumps({"status": "ok", "time": time.time(), "platform": sys.platform}).encode())
            return

        # Static files
        if path in ("/", "/index.html"):
            fp = STATIC_DIR / "index.html"
            if fp.exists():
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                for k, v in cors_headers.items(): self.send_header(k, v)
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
                for k, v in cors_headers.items(): self.send_header(k, v)
                self.end_headers()
                self.wfile.write(fp.read_bytes())
                return

        if path.startswith("/") and (STATIC_DIR / path.lstrip("/")).exists():
            fp = STATIC_DIR / path.lstrip("/")
            if fp.is_file():
                ctype = "text/plain"
                if fp.suffix == ".js": ctype = "application/javascript"
                elif fp.suffix == ".css": ctype = "text/css"
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                for k, v in cors_headers.items(): self.send_header(k, v)
                self.end_headers()
                self.wfile.write(fp.read_bytes())
                return

        self.send_response(404)
        self.send_header("Content-Type", "application/json")
        for k, v in cors_headers.items(): self.send_header(k, v)
        self.end_headers()
        self.wfile.write(json.dumps({"error": "not found", "path": path}).encode())

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

def find_free_port(start=9090, max_try=20):
    import socket
    for p in range(start, start + max_try):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("0.0.0.0", p))
                return p
            except OSError:
                continue
    return start

def main():
    import argparse
    parser = argparse.ArgumentParser(description="System Monitor Dashboard (Universal)")
    parser.add_argument("--port", type=int, default=None, help="Port to listen (default auto 9090)")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind")
    parser.add_argument("--projects-root", default=None, help="Root directory to scan for projects (default: auto)")
    args = parser.parse_args()

    if args.projects_root:
        global HOME_DIR
        HOME_DIR = Path(args.projects_root).resolve()

    port = args.port or int(os.environ.get("PORT") or 0) or find_free_port(9090)
    if port == 0:
        port = find_free_port(9090)

    import socket
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind((args.host, port))
    except OSError:
        old = port
        port = find_free_port(port + 1)
        print(f"Port {old} in use, using {port} instead")

    ThreadingHTTPServer.allow_reuse_address = True
    server = ThreadingHTTPServer((args.host, port), MonitorHandler)

    def warmup():
        try:
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

    os_desc = "Windows Native" if IS_WINDOWS else "Linux Native"
    print(f"System Monitor running ({os_desc}) at http://{args.host}:{port}")
    print(f"  Dashboard:     http://localhost:{port}/")
    print(f"  API:           http://localhost:{port}/api/stats")
    print(f"  Stream:        http://localhost:{port}/api/stream")
    print(f"  Projects Root: {HOME_DIR}")
    print(f"  Press Ctrl+C to stop")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
        server.shutdown()

if __name__ == "__main__":
    main()
