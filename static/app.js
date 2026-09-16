let cpuChart, netChart;
let historyLen = 60;
let cpuHistory = Array(historyLen).fill(0);
let ramHistory = Array(historyLen).fill(0);
let rxHistory = Array(historyLen).fill(0);
let txHistory = Array(historyLen).fill(0);
let labels = Array(historyLen).fill('');
let lastNet = {rx:0, tx:0};
let procSort = 'cpu';
let allProjects = [];
let netIface = 'eth0';

function humanBytes(b) {
  if (b===undefined || b===null) return '-';
  const units = ['B','KB','MB','GB','TB'];
  let i=0;
  let v=b;
  while(v>=1024 && i<units.length-1){ v/=1024; i++; }
  return i===0 ? `${v} B` : `${v.toFixed(v>=100?0:v>=10?1:2)} ${units[i]}`;
}
function humanRate(bps){
  if(!bps || bps<1) return '0 B/s';
  return humanBytes(bps)+'/s';
}
function humanPercent(p){ return `${p.toFixed(1)}%`; }
function escapeHtml(s){
  if(s===null||s===undefined) return '';
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
}

function initCharts(){
  const ctx1 = document.getElementById('cpuChart').getContext('2d');
  cpuChart = new Chart(ctx1, {
    type: 'line',
    data: {
      labels: labels,
      datasets: [
        { label:'CPU', data: cpuHistory, borderColor:'#06b6d4', backgroundColor:'rgba(6,182,214,0.08)', borderWidth:2, tension:0.35, fill:true, pointRadius:0 },
        { label:'RAM', data: ramHistory, borderColor:'#8b5cf6', backgroundColor:'rgba(139,92,246,0.06)', borderWidth:2, tension:0.35, fill:true, pointRadius:0 }
      ]
    },
    options: {
      responsive:true, maintainAspectRatio:false,
      interaction:{ mode:'index', intersect:false },
      plugins:{ legend:{ display:false }, tooltip:{ backgroundColor:'#151b24', titleColor:'#e5e7eb', bodyColor:'#9ca3af', borderColor:'#1f2937', borderWidth:1 }},
      scales:{
        x:{ display:false, grid:{ display:false } },
        y:{ min:0, max:100, ticks:{ color:'#6b7280', stepSize:25, callback:v=>v+'%' }, grid:{ color:'rgba(31,41,55,0.6)' } }
      },
      animation:false
    }
  });
  const ctx2 = document.getElementById('netChart').getContext('2d');
  netChart = new Chart(ctx2, {
    type: 'line',
    data: {
      labels: labels,
      datasets: [
        { label:'RX', data: rxHistory, borderColor:'#10b981', backgroundColor:'rgba(16,185,129,0.08)', borderWidth:2, tension:0.35, fill:true, pointRadius:0 },
        { label:'TX', data: txHistory, borderColor:'#3b82f6', backgroundColor:'rgba(59,130,246,0.06)', borderWidth:2, tension:0.35, fill:true, pointRadius:0 }
      ]
    },
    options: {
      responsive:true, maintainAspectRatio:false,
      interaction:{ mode:'index', intersect:false },
      plugins:{ legend:{ display:false }, tooltip:{ backgroundColor:'#151b24', titleColor:'#e5e7eb', bodyColor:'#9ca3af', borderColor:'#1f2937', borderWidth:1, callbacks:{ label:(ctx)=> `${ctx.dataset.label}: ${humanRate(ctx.parsed.y)}` }}},
      scales:{
        x:{ display:false, grid:{ display:false } },
        y:{ beginAtZero:true, ticks:{ color:'#6b7280', callback:v=>humanRate(v) }, grid:{ color:'rgba(31,41,55,0.6)' } }
      },
      animation:false
    }
  });
}

function updateHeader(data){
  document.getElementById('hostname').textContent = data.uptime.hostname || '-';
  document.getElementById('os-info').textContent = data.uptime.os || '';
  document.getElementById('uptime').textContent = data.uptime.uptime_human || '-';
  document.getElementById('kernel-info').textContent = `kernel ${data.uptime.kernel || ''}`;
  document.getElementById('system-host').textContent = data.uptime.hostname || '-';
  document.getElementById('proc-count').textContent = data.processes.total || '0';
  document.getElementById('user-count').textContent = data.uptime.users || '0';
  document.getElementById('last-update').textContent = data.timestamp_human || '';
  document.getElementById('current-time').textContent = new Date().toLocaleTimeString('id-ID');
}

function updateCpu(data){
  const cpu = data.cpu;
  document.getElementById('cpu-usage').textContent = cpu.usage.toFixed(1) + '%';
  document.getElementById('cpu-model').textContent = `${cpu.model.split('@')[0].trim()} • ${cpu.cores} cores`;
  document.getElementById('cpu-mhz').textContent = `${cpu.mhz} MHz`;
  document.getElementById('loadavg').textContent = cpu.loadavg.map(v=>v.toFixed(2)).join(' ');
  document.getElementById('cpu-bar').style.width = cpu.usage + '%';
  // color based on usage
  const bar = document.getElementById('cpu-bar');
  if(cpu.usage>85) bar.className='h-full bg-gradient-to-r from-red-500 to-orange-500 rounded-full transition-all duration-500';
  else if(cpu.usage>65) bar.className='h-full bg-gradient-to-r from-amber-500 to-orange-500 rounded-full transition-all duration-500';
  else bar.className='h-full bg-gradient-to-r from-cyan-500 to-blue-500 rounded-full transition-all duration-500';

  const container = document.getElementById('cpu-cores');
  container.innerHTML = '';
  cpu.per_core.forEach((u,i)=>{
    const col = u>80 ? 'bg-red-500' : u>60 ? 'bg-amber-500' : u>30 ? 'bg-cyan-500' : 'bg-emerald-500';
    const el = document.createElement('div');
    el.className='flex flex-col items-center gap-1 min-w-0';
    el.innerHTML = `<div class="w-full h-1.5 bg-[#0a0e14] rounded-full overflow-hidden"><div class="h-full ${col} transition-all" style="width:${u}%"></div></div><span class="text-[9px] sm:text-[10px] font-mono text-gray-500 truncate w-full text-center">C${i} ${u}%</span>`;
    container.appendChild(el);
  });
}

function updateMemory(data){
  const m = data.memory;
  document.getElementById('ram-usage').textContent = m.percent.toFixed(1)+'%';
  document.getElementById('ram-detail').textContent = `${humanBytes(m.used)} / ${humanBytes(m.total)}`;
  document.getElementById('ram-available').textContent = humanBytes(m.available);
  document.getElementById('swap-usage').textContent = m.swap_total ? humanPercent(m.swap_percent) : 'none';
  document.getElementById('ram-bar').style.width = m.percent+'%';
  const bar = document.getElementById('ram-bar');
  if(m.percent>85) bar.className='h-full bg-gradient-to-r from-red-500 to-pink-500 rounded-full transition-all duration-500';
  else if(m.percent>70) bar.className='h-full bg-gradient-to-r from-amber-500 to-orange-500 rounded-full transition-all duration-500';
  else bar.className='h-full bg-gradient-to-r from-violet-500 to-purple-500 rounded-full transition-all duration-500';
  document.getElementById('ram-extra').textContent = `Buffers ${humanBytes(m.buffers)} • Cached ${humanBytes(m.cached)}`;
}

function updateDisks(data){
  const disks = data.disks;
  // main card - root
  const root = disks.find(d=>d.mount==='/') || disks[0];
  if(root){
    document.getElementById('disk-usage').textContent = root.percent.toFixed(1)+'%';
    document.getElementById('disk-detail').textContent = `${root.mount} ${humanBytes(root.used)} used`;
    document.getElementById('disk-free').textContent = `Free ${humanBytes(root.free)}`;
    document.getElementById('disk-fstype').textContent = root.fstype;
    document.getElementById('disk-bar').style.width = root.percent+'%';
    const bar=document.getElementById('disk-bar');
    if(root.percent>85) bar.className='h-full bg-gradient-to-r from-red-500 to-orange-500 rounded-full transition-all duration-500';
    else bar.className='h-full bg-gradient-to-r from-amber-500 to-orange-500 rounded-full transition-all duration-500';
  }
  // disk io main
  const dio = data.disk_io;
  if(dio && dio.length){
    // sum or main sda
    const main = dio.find(d=>d.device.startsWith('sda')) || dio[0];
    if(main) document.getElementById('disk-io').textContent = `R ${humanRate(main.read_rate)} • W ${humanRate(main.write_rate)}`;
  }
  // disks list
  const list = document.getElementById('disks-list');
  list.innerHTML='';
  disks.forEach(d=>{
    const pct = d.percent;
    const col = pct>85 ? 'from-red-500 to-orange-500' : pct>70 ? 'from-amber-500 to-orange-500' : 'from-emerald-500 to-teal-500';
    const el = document.createElement('div');
    el.className='bg-[#0a0e14] border border-[#1f2937] rounded-lg p-3';
    el.innerHTML = `
      <div class="flex justify-between items-start mb-2">
        <div>
          <p class="font-mono text-xs font-semibold text-gray-300">${escapeHtml(d.mount)} <span class="text-gray-600 font-normal">${escapeHtml(d.device)}</span></p>
          <p class="text-[11px] text-gray-500">${escapeHtml(d.fstype)} • ${humanBytes(d.used)} / ${humanBytes(d.total)}</p>
        </div>
        <span class="text-xs font-mono font-bold ${pct>85?'text-red-400':pct>70?'text-amber-400':'text-emerald-400'}">${pct.toFixed(1)}%</span>
      </div>
      <div class="w-full h-1.5 bg-[#151b24] rounded-full overflow-hidden">
        <div class="h-full bg-gradient-to-r ${col} rounded-full" style="width:${pct}%"></div>
      </div>
      <p class="text-[10px] text-gray-600 mt-1 font-mono">inodes ${d.inodes_percent.toFixed(1)}% • free ${humanBytes(d.free)}</p>
    `;
    list.appendChild(el);
  });
  // disk io list
  const ioList = document.getElementById('disk-io-list');
  ioList.innerHTML='';
  (data.disk_io||[]).slice(0,5).forEach(d=>{
    const row=document.createElement('div');
    row.className='flex justify-between items-center bg-[#0a0e14] border border-[#1f2937] rounded-lg px-2 py-1.5';
    row.innerHTML=`<span class="font-semibold text-gray-400">${escapeHtml(d.device)}</span><span class="text-gray-500">R ${humanRate(d.read_rate)} • W ${humanRate(d.write_rate)}</span>`;
    ioList.appendChild(row);
  });
}

function updateNetwork(data){
  const nets = data.network || [];
  // choose main iface eth0 else first non-lo
  let main = nets.find(n=>n.iface==='eth0') || nets.find(n=>n.iface!=='lo') || nets[0];
  if(main){
    document.getElementById('net-rx').textContent = humanRate(main.rx_rate);
    document.getElementById('net-tx').textContent = humanRate(main.tx_rate);
    document.getElementById('net-iface').textContent = main.iface;
    netIface = main.iface;
  }
  const list=document.getElementById('net-list');
  list.innerHTML='';
  nets.slice(0,6).forEach(n=>{
    const div=document.createElement('div');
    div.className='bg-[#0a0e14] border border-[#1f2937] rounded-lg px-2 py-2';
    div.innerHTML=`
      <div class="flex justify-between font-semibold"><span class="text-gray-300">${escapeHtml(n.iface)}</span><span class="text-gray-500 font-mono text-[11px]">${humanBytes(n.rx_bytes)} / ${humanBytes(n.tx_bytes)}</span></div>
      <div class="flex justify-between text-[11px] font-mono mt-1"><span class="text-emerald-400">↓ ${humanRate(n.rx_rate)}</span><span class="text-blue-400">↑ ${humanRate(n.tx_rate)}</span></div>
    `;
    list.appendChild(div);
  });
}

function updateProcesses(data){
  const procs = procSort==='cpu' ? data.processes.by_cpu : data.processes.by_mem;
  const body=document.getElementById('proc-body');
  body.innerHTML='';
  (procs||[]).forEach(p=>{
    const tr=document.createElement('tr');
    tr.className='hover:bg-[#0a0e14]/50';
    const cpuCol = p.cpu>50?'text-red-400':p.cpu>20?'text-amber-400':'text-gray-300';
    const memCol = p.mem>10?'text-violet-400':'text-gray-300';
    tr.innerHTML=`
      <td class="py-2 text-gray-500">${p.pid}</td>
      <td class="py-2 max-w-[90px] truncate text-gray-300 font-semibold">${escapeHtml(p.name)}</td>
      <td class="py-2 text-right font-bold ${cpuCol}">${p.cpu.toFixed(1)}%</td>
      <td class="py-2 text-right ${memCol}">${p.mem.toFixed(1)}%</td>
      <td class="py-2 hidden sm:table-cell max-w-[180px] truncate text-gray-500" title="${escapeHtml(p.cmd)}">${escapeHtml((p.cmd||'').slice(0,70))}</td>
    `;
    body.appendChild(tr);
  });
}

function badgeForType(t){
  const map={
    'docker':'badge-docker',
    'python':'badge-python',
    'node':'badge-node',
    'dotnet':'badge-dotnet',
    'minecraft':'badge-minecraft',
    'valheim':'badge-valheim',
    'git':'badge-git',
    'generic':'badge-generic',
    'monorepo':'badge-generic'
  };
  const cls=map[t]||'badge-generic';
  const label = t==='dotnet' ? '.NET' : t;
  return `<span class="badge ${cls}">${label}</span>`;
}

function updateProjects(data){
  allProjects = data.projects || [];
  document.getElementById('project-count').textContent = allProjects.length;
  renderProjects();
}

function renderProjects(){
  const body=document.getElementById('projects-body');
  const search=document.getElementById('project-search').value.toLowerCase();
  const filter=document.getElementById('project-filter').value;
  let filtered = allProjects.filter(p=>{
    if(search && !p.name.toLowerCase().includes(search) && !p.types.join(',').toLowerCase().includes(search)) return false;
    if(filter==='all') return true;
    if(filter==='running') return p.running;
    return p.types.includes(filter);
  });
  if(!filtered.length){
    body.innerHTML=`<tr><td colspan="5" class="py-8 text-center text-gray-600">Tidak ada project sesuai filter</td></tr>`;
    return;
  }
  body.innerHTML='';
  filtered.forEach(p=>{
    const tr=document.createElement('tr');
    tr.className='hover:bg-[#0a0e14]/60 transition-colors';
    const statusDot = p.running ? '<span class="status-dot status-running"></span><span class="text-emerald-400">Running</span>' : '<span class="status-dot status-stopped"></span><span class="text-gray-500">Idle</span>';
    // if docker container running but project idle? already captured
    const typesHtml = (p.types||[]).slice(0,3).map(badgeForType).join(' ');
    const git = p.git_branch ? `<span class="font-mono text-xs bg-[#0a0e14] border border-[#1f2937] px-1.5 py-0.5 rounded text-gray-400">${escapeHtml(p.git_branch)}</span> <span class="text-[11px] ${p.git_status==='clean'?'text-emerald-400':'text-amber-400'}">${escapeHtml(p.git_status)}</span>` : '<span class="text-gray-600">-</span>';
    const services = p.services && p.services.length ? p.services.map(s=>`<span class="text-[11px] bg-[#1f2937] px-1.5 py-0.5 rounded text-gray-400">${escapeHtml(s)}</span>`).join(' ') : '<span class="text-gray-600">-</span>';
    tr.innerHTML=`
      <td class="py-3">
        <div class="font-semibold text-gray-200 flex items-center gap-2">
          <i class="fas fa-folder text-gray-600 text-xs"></i> ${escapeHtml(p.name)}
        </div>
        <div class="text-[11px] text-gray-600 font-mono truncate max-w-[220px]">${escapeHtml(p.path)}</div>
        <div class="text-[11px] text-gray-500 hidden sm:hidden">${typesHtml}</div>
      </td>
      <td class="py-3 hidden sm:table-cell"><div class="flex flex-wrap gap-1">${typesHtml}</div><div class="mt-1 hidden lg:block">${services}</div></td>
      <td class="py-3 hidden md:table-cell font-mono text-xs text-gray-400">${escapeHtml(p.size_human||'-')}</td>
      <td class="py-3 hidden lg:table-cell">${git}<div class="text-[11px] text-gray-600 mt-1">${escapeHtml(p.last_modified||'')}</div></td>
      <td class="py-3"><div class="flex items-center gap-1.5 text-xs">${statusDot}</div><div class="text-[11px] text-gray-600 md:hidden mt-1">${escapeHtml(p.size_human||'')}</div></td>
    `;
    body.appendChild(tr);
  });
}

function updateDocker(data){
  const d=data.docker;
  if(!d || !d.available){
    document.getElementById('docker-body').innerHTML=`<tr><td colspan="5" class="py-6 text-center text-gray-600">Docker tidak tersedia</td></tr>`;
    return;
  }
  document.getElementById('docker-count').textContent=d.count;
  document.getElementById('docker-running').textContent=`${d.running} running`;
  const body=document.getElementById('docker-body');
  body.innerHTML='';
  if(!d.containers || !d.containers.length){
    document.getElementById('docker-empty').classList.remove('hidden');
    return;
  }
  document.getElementById('docker-empty').classList.add('hidden');
  d.containers.forEach(c=>{
    const isUp = c.status.includes('Up');
    const dot = isUp ? 'status-running' : 'status-stopped';
    const tr=document.createElement('tr');
    tr.className='hover:bg-[#0a0e14]/50';
    tr.innerHTML=`
      <td class="py-2"><div class="flex items-center gap-2"><span class="status-dot ${dot}"></span><span class="font-semibold text-gray-300">${escapeHtml(c.name)}</span></div><div class="text-[11px] text-gray-600 font-mono">${escapeHtml(c.id)}</div></td>
      <td class="py-2 hidden sm:table-cell text-gray-500 max-w-[170px] truncate" title="${escapeHtml(c.image)}">${escapeHtml(c.image)}</td>
      <td class="py-2"><span class="text-[11px] ${isUp?'text-emerald-400':'text-gray-500'}">${escapeHtml(c.status.slice(0,30))}</span><div class="text-[11px] text-gray-600 hidden md:block">${escapeHtml(c.ports?c.ports.slice(0,40):'')}</div></td>
      <td class="py-2 hidden md:table-cell text-right font-mono text-cyan-400">${escapeHtml(c.cpu||'-')}</td>
      <td class="py-2 hidden md:table-cell text-right font-mono text-violet-400">${escapeHtml(c.mem_perc||c.mem_usage||'-')}</td>
    `;
    body.appendChild(tr);
  });
}

function updateCharts(data){
  // cpu/ram
  cpuHistory.shift(); cpuHistory.push(data.cpu.usage);
  ramHistory.shift(); ramHistory.push(data.memory.percent);
  const nowLabel = new Date().toLocaleTimeString('id-ID');
  labels.shift(); labels.push(nowLabel);
  cpuChart.update('none');

  // net
  const main = (data.network||[]).find(n=>n.iface===netIface) || (data.network||[]).find(n=>n.iface!=='lo') || (data.network||[])[0];
  if(main){
    rxHistory.shift(); rxHistory.push(main.rx_rate);
    txHistory.shift(); txHistory.push(main.tx_rate);
    netChart.update('none');
  }
}

let sse = null;
function connectSSE(){
  const statusEl=document.getElementById('conn-status');
  if(sse) sse.close();
  statusEl.textContent='CONNECTING';
  statusEl.className='text-amber-400 font-medium text-xs';
  const proto = location.protocol;
  const url = `${proto}//${location.hostname}:${location.port}/api/stream?interval=1`;
  // Actually use relative
  sse = new EventSource('/api/stream?interval=1');
  sse.onopen = ()=>{
    statusEl.textContent='LIVE';
    statusEl.className='text-emerald-400 font-medium text-xs';
  };
  sse.onmessage = (e)=>{
    try{
      const data=JSON.parse(e.data);
      handleData(data);
    }catch(err){ console.error(err); }
  };
  sse.onerror = ()=>{
    statusEl.textContent='RECONNECTING';
    statusEl.className='text-red-400 font-medium text-xs';
    sse.close();
    setTimeout(connectSSE, 2000);
    // fallback to polling
    fallbackPoll();
  };
}
let pollTimer=null;
function fallbackPoll(){
  if(pollTimer) return;
  pollTimer=setInterval(async()=>{
    try{
      const r=await fetch('/api/stats');
      const data=await r.json();
      handleData(data);
      document.getElementById('conn-status').textContent='POLLING';
      document.getElementById('conn-status').className='text-blue-400 font-medium text-xs';
    }catch(e){}
  }, 2000);
}
function handleData(data){
  updateHeader(data);
  updateCpu(data);
  updateMemory(data);
  updateDisks(data);
  updateNetwork(data);
  updateProcesses(data);
  updateDocker(data);
  updateProjects(data);
  updateCharts(data);
}

function setProcSort(s){
  procSort=s;
  document.getElementById('btn-cpu').className = s==='cpu' ? 'px-2.5 py-1 rounded-lg text-xs font-medium bg-blue-600 text-white' : 'px-2.5 py-1 rounded-lg text-xs font-medium bg-[#1f2937] text-gray-400 hover:bg-[#232f45]';
  document.getElementById('btn-mem').className = s==='mem' ? 'px-2.5 py-1 rounded-lg text-xs font-medium bg-blue-600 text-white' : 'px-2.5 py-1 rounded-lg text-xs font-medium bg-[#1f2937] text-gray-400 hover:bg-[#232f45]';
}

function toggleTheme(){ document.documentElement.classList.toggle('dark'); }

document.addEventListener('DOMContentLoaded', ()=>{
  initCharts();
  connectSSE();
  document.getElementById('project-search').addEventListener('input', renderProjects);
  document.getElementById('project-filter').addEventListener('change', renderProjects);
  // fallback if SSE fails immediately due to no support
  setTimeout(()=>{
    if(!sse || sse.readyState===2) fallbackPoll();
  }, 4000);
});
