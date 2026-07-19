#!/usr/bin/env python3
"""Sirius local control panel + reverse proxy.

Serves a self-contained control UI at http://localhost:8777 and proxies
  /api/*   -> http://<ROBOT>:8088/api/*   (REST)
  /stream  -> http://<ROBOT>:8080/video_stream (MJPEG)
so the browser only ever talks to localhost => no CORS, no mixed-content.
"""
import sys, json, os, time, shutil, threading, subprocess, urllib.request, urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROBOT = sys.argv[1] if len(sys.argv) > 1 else "192.168.4.134"
PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 8777
API = f"http://{ROBOT}:8088"
CAM = f"http://{ROBOT}:8080/video_stream"

# Voice control (the local brain) — supervised as a child process; its JSONL events feed the UI monitor.
VOICE_DIR = os.environ.get("VOICE_DIR", os.path.expanduser("~/sirius-voice-bridge"))
VOICE_PY = os.path.join(VOICE_DIR, ".venv", "bin", "python")
VOICE_EVENTS = os.path.join(VOICE_DIR, "voice_direct.events.jsonl")
_voice = {"proc": None}
_voice_lock = threading.Lock()

# One venv-side probe for the deps/mic/models readiness (cheaper than 4 python startups).
PROBE = (
    "import json,os,glob\nr={}\n"
    "try:\n import sounddevice,openwakeword,faster_whisper,webrtcvad,onnxruntime,numpy\n r['deps']=True\n"
    "except Exception:\n r['deps']=False\n"
    "try:\n import sounddevice as sd; sd.query_devices(kind='input'); r['mic']=True\n"
    "except Exception:\n r['mic']=False\n"
    "try:\n import openwakeword as o; r['wake']=os.path.exists(os.path.join(o.__path__[0],'resources','models','hey_jarvis_v0.1.onnx'))\n"
    "except Exception:\n r['wake']=False\n"
    "r['whisper']=bool(glob.glob(os.path.expanduser('~/.cache/huggingface/hub/*faster-whisper-small*')))\n"
    "print(json.dumps(r))\n"
)

HTML = r"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sirius Control</title>
<style>
  :root{--bg:#0e1116;--panel:#171b22;--panel2:#1e242d;--line:#2a313c;--fg:#e6edf3;--mut:#8b97a7;--acc:#4a9eff;--ok:#3fb950;--warn:#e3b341;--bad:#f85149;}
  *{box-sizing:border-box}
  body{margin:0;font:14px/1.4 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;background:var(--bg);color:var(--fg)}
  header{display:flex;align-items:center;gap:14px;padding:10px 16px;background:var(--panel);border-bottom:1px solid var(--line);position:sticky;top:0;z-index:5}
  header h1{font-size:15px;margin:0;font-weight:600;letter-spacing:.3px}
  .pill{font-size:12px;color:var(--mut);background:var(--panel2);border:1px solid var(--line);border-radius:20px;padding:3px 10px;white-space:nowrap}
  .wrap{display:grid;grid-template-columns:minmax(320px,1fr) minmax(340px,1.1fr);gap:16px;padding:16px;max-width:1200px;margin:0 auto}
  @media(max-width:820px){.wrap{grid-template-columns:1fr}}
  .card{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:14px}
  .card h2{font-size:12px;text-transform:uppercase;letter-spacing:.6px;color:var(--mut);margin:0 0 10px}
  .cam{width:100%;background:#000;border-radius:10px;aspect-ratio:4/3;object-fit:contain;display:block}
  .stat{display:flex;gap:10px;flex-wrap:wrap;margin-top:10px}
  .stat .box{flex:1;min-width:90px;background:var(--panel2);border:1px solid var(--line);border-radius:9px;padding:8px 10px}
  .stat .box .k{font-size:11px;color:var(--mut)}
  .stat .box .v{font-size:18px;font-weight:600;margin-top:2px}
  .grid3{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;max-width:280px;margin:0 auto}
  button{font:inherit;color:var(--fg);background:var(--panel2);border:1px solid var(--line);border-radius:9px;padding:12px 8px;cursor:pointer;transition:.08s;user-select:none;-webkit-user-select:none}
  button:hover{border-color:var(--acc)}
  button:active{background:var(--acc);border-color:var(--acc);transform:translateY(1px)}
  .stopbtn{background:#3a1c1e;border-color:#5c2a2d;color:#ff9a9a}
  .stopbtn:active{background:var(--bad)}
  .row{display:flex;align-items:center;gap:10px;margin:8px 0}
  .row label{font-size:12px;color:var(--mut);width:64px}
  input[type=range]{flex:1;accent-color:var(--acc)}
  .val{font-size:12px;color:var(--fg);width:44px;text-align:right;font-variant-numeric:tabular-nums}
  .actions-head{display:flex;gap:8px;align-items:center;margin-bottom:8px}
  #search{flex:1;background:var(--panel2);border:1px solid var(--line);color:var(--fg);border-radius:8px;padding:8px 10px}
  .alist{max-height:340px;overflow:auto;display:grid;grid-template-columns:1fr 1fr;gap:6px}
  .alist button{padding:8px;text-align:left;font-size:12.5px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .cat{grid-column:1/-1;font-size:10.5px;text-transform:uppercase;letter-spacing:.5px;color:var(--mut);margin:6px 2px 0}
  .seg{display:flex;gap:6px;flex-wrap:wrap}
  .seg button{padding:7px 11px;font-size:12.5px;flex:0 0 auto}
  .seg button.on{background:var(--acc);border-color:var(--acc);color:#fff}
  .toast{position:fixed;bottom:16px;left:50%;transform:translateX(-50%);background:var(--panel2);border:1px solid var(--line);border-radius:8px;padding:8px 14px;font-size:13px;opacity:0;transition:.2s;pointer-events:none;max-width:80vw}
  .toast.show{opacity:1}
  .charging{color:var(--ok)} .discharging{color:var(--warn)}
  .muted{color:var(--mut);font-size:12px}
</style></head>
<body>
<header>
  <h1>🐕 Sirius Control</h1>
  <span class="pill" id="fw">robot —</span>
  <span class="pill" id="batt">battery —</span>
  <span class="pill" id="conn">…</span>
  <span class="pill" id="pose">pose —</span>
  <button id="autoBtn" class="pill" style="cursor:pointer;border-color:var(--ok)">🤖 autonomous: on</button>
  <button id="modeBtn" class="pill" style="cursor:pointer">🖥️ desktop</button>
</header>
<div class="wrap">
  <div class="col">
    <div class="card">
      <h2>Camera <button id="camBtn" class="pill" style="cursor:pointer;float:right;color:var(--ok)">on</button></h2>
      <img class="cam" id="cam" alt="camera stream">
      <div class="stat">
        <div class="box"><div class="k">Battery</div><div class="v" id="pct">—</div></div>
        <div class="box"><div class="k">Charge</div><div class="v" id="chg">—</div></div>
        <div class="box"><div class="k">Motors °C</div><div class="v" id="temp">—</div></div>
      </div>
      <div class="muted" id="voltage" style="margin-top:8px"></div>
    </div>
    <div class="card" style="margin-top:16px">
      <h2>Move</h2>
      <div class="grid3">
        <button data-hold="gait/move/turn-left">↖ turn</button>
        <button data-hold="gait/move/forward">▲ fwd</button>
        <button data-hold="gait/move/turn-right">turn ↗</button>
        <button data-hold="gait/move/left">◀ left</button>
        <button class="stopbtn" id="gstop">■ stop</button>
        <button data-hold="gait/move/right">right ▶</button>
        <button></button>
        <button data-hold="gait/move/backward">▼ back</button>
        <button></button>
      </div>
      <div class="row"><label>Speed</label><input type="range" id="speed" min="0.05" max="0.5" step="0.01" value="0.15"><span class="val" id="speedv">0.15</span></div>
      <div class="row"><label>Gait</label><div class="seg" id="modes">
        <button data-mode="default" class="on">default</button>
        <button data-mode="slow">slow</button>
        <button data-mode="walk">walk</button>
        <button data-mode="gait">fast</button>
        <button data-mode="precision">precision</button>
        <button data-mode="climb">climb</button>
      </div></div>
      <div class="row"><label>Rest</label><div class="seg">
        <button id="sleepBtn">😴 sleep</button>
        <button id="wakeBtn">🧍 wake</button>
        <span class="muted" style="align-self:center">(full power-off = physical button)</span>
      </div></div>
      <div class="row"><label>Recover</label><div class="seg">
        <button id="recLeft">⤾ tipped left</button>
        <button id="recRight">tipped right ⤿</button>
        <span class="muted" style="align-self:center">(for side tips, not fully upside-down)</span>
      </div></div>
    </div>
  </div>
  <div class="col">
    <div class="card">
      <h2>Pose</h2>
      <div class="row"><label>Head yaw</label><input type="range" id="hyaw" min="-40" max="40" step="1" value="0"><span class="val" id="hyawv">0</span></div>
      <div class="row"><label>Head pitch</label><input type="range" id="hpitch" min="-30" max="30" step="1" value="0"><span class="val" id="hpitchv">0</span></div>
      <div class="row"><label>Body pitch</label><input type="range" id="bpitch" min="-15" max="15" step="1" value="0"><span class="val" id="bpitchv">0</span></div>
      <div class="row"><label>Body roll</label><input type="range" id="broll" min="-15" max="15" step="1" value="0"><span class="val" id="brollv">0</span></div>
      <div class="row"><label>Body yaw</label><input type="range" id="byaw" min="-20" max="20" step="1" value="0"><span class="val" id="byawv">0</span></div>
      <div class="seg" style="margin-top:6px"><button id="poseReset">reset pose</button></div>
    </div>
    <div class="card" style="margin-top:16px">
      <h2>Actions <span class="muted" id="acount"></span></h2>
      <div class="actions-head">
        <input id="search" placeholder="filter actions… (wave, dance, sit, lie)">
        <button class="stopbtn" id="astop">stop</button>
      </div>
      <div class="row"><label>Torque</label><input type="range" id="torque" min="200" max="2047" step="1" value="1500"><span class="val" id="torquev">1500</span></div>
      <div class="alist" id="alist"><div class="muted">loading actions…</div></div>
    </div>
    <div class="card" style="margin-top:16px">
      <h2>Voice — &ldquo;Hey Jarvis&rdquo; <button id="voiceBtn" class="pill" style="cursor:pointer;float:right">off</button></h2>
      <div class="muted" id="vinfo">local brain stopped</div>
      <div style="margin-top:8px;font-size:12px">Readiness
        <a id="vrecheck" style="color:var(--acc);cursor:pointer;margin-left:6px">recheck</a>
        <a id="vsetup" style="color:var(--acc);cursor:pointer;margin-left:12px">set up / install</a>
        <div id="vhealth" style="margin-top:6px"><span class="muted">checking…</span></div></div>
      <pre id="vsetuplog" style="display:none;margin-top:8px;max-height:150px;overflow:auto;background:#0b0e13;border:1px solid var(--line);border-radius:8px;padding:8px;font:11px/1.5 ui-monospace,Menlo,monospace;white-space:pre-wrap"></pre>
      <div id="vfeed" style="margin-top:10px;height:170px;overflow:auto;background:var(--panel2);border:1px solid var(--line);border-radius:8px;padding:8px;font:12px/1.55 ui-monospace,Menlo,monospace"></div>
    </div>
  </div>
</div>
<div class="toast" id="toast"></div>
<script>
const $=s=>document.querySelector(s);
let toastT;
function toast(m,bad){const t=$('#toast');t.textContent=m;t.style.borderColor=bad?'#5c2a2d':'';t.classList.add('show');clearTimeout(toastT);toastT=setTimeout(()=>t.classList.remove('show'),1600);}
async function api(path,method='GET',body){
  try{
    const o={method,headers:{}};
    if(body!==undefined){o.headers['Content-Type']='application/json';o.body=JSON.stringify(body);}
    const r=await fetch('/api/v1/'+path,o);
    const j=await r.json().catch(()=>({}));
    if(j&&j.success===false) toast((j.message||'error')+' ('+path+')',true);
    return j;
  }catch(e){toast('unreachable: '+path,true);$('#conn').textContent='offline';$('#conn').className='pill discharging';return null;}
}
// camera — the MJPEG stream only emits frames when vision detection is on.
// Reconnect with exponential backoff so a downed camera can't be hammered
// (each reconnect opens a stream on the robot; tight-looping leaks connections).
let camOn=true, camRetry=0, camTimer=null;
function camConnect(){ if(!camOn)return; camTimer=null; $('#cam').src='/stream?'+Date.now(); }
async function setCam(on){camOn=on; camRetry=0; if(camTimer){clearTimeout(camTimer);camTimer=null;}
  $('#camBtn').textContent=on?'on':'off';$('#camBtn').style.color=on?'var(--ok)':'var(--mut)';
  try{await api('vision/detection','POST',{enabled:on});}catch(e){}
  if(on)camConnect(); else {$('#cam').removeAttribute('src'); $('#cam').style.opacity=.4;}}
$('#cam').onload=()=>{camRetry=0; $('#cam').style.opacity=1;};   // frames flowing → reset backoff
$('#cam').onerror=()=>{ if(!camOn||camTimer)return; camRetry++;
  const delay=Math.min(30000, 1000*Math.pow(2,Math.min(camRetry,5)));  // 2s,4s,8s,16s,32s→cap 30s
  camTimer=setTimeout(camConnect, delay); };
setCam(true);
$('#camBtn').onclick=()=>setCam(!camOn);
// status poll
async function poll(){
  const b=await api('battery/status');
  if(b&&b.data){const d=b.data;const pc=Math.round(d.percentage*100);
    // NOTE: this robot's is_charging / power_supply_status are unreliable — they stay
    // "CHARGING" even when unplugged. Trust the CURRENT sign: >0 = actually taking charge.
    const charging=d.current>0.1;
    $('#pct').textContent=pc+'%';$('#batt').textContent='battery '+pc+(charging?' ⚡':'');
    $('#chg').innerHTML=(d.current>0.05?'<span class="charging">↑ '+d.current.toFixed(2)+'A</span>':(d.current<-0.05?'<span class="discharging">↓ '+d.current.toFixed(2)+'A</span>':'flat'));
    $('#voltage').textContent=d.voltage.toFixed(2)+' V · '+(d.power_supply_status_string||'')+' · '+(d.power_supply_health_string||'');
    $('#conn').textContent='online';$('#conn').className='pill charging';
    // Charger plugs into the tail => actually-charging means it's tethered/on a leash.
    // Keep it in desktop mode while charging so it can't wander off and unplug itself.
    if(charging && robotMode!=='desktop'){ setMode('desktop'); toast('charging → desktop mode (tethered)'); }
  }
  const t=await api('motor/temperature');
  if(t&&t.data){const d=t.data;const vals=[d.front_left,d.front_right,d.back_left,d.back_right].filter(x=>x!=null);
    const mx=vals.length?Math.max(...vals):0;
    // firmware reports all-zero when motor telemetry isn't populated (idle/relaxed) — show "—", not a fake 0°
    $('#temp').textContent = mx>0 ? mx+'°' : '—';}
  // current pose/stance: sleep action => asleep, else derive from body height (tran_z)
  const tr=await api('transform/status'); const ac=await api('action/status');
  $('#pose').textContent='pose: '+poseLabel(tr&&tr.data, ac&&ac.data);
}
function poseLabel(tr,ac){
  const f=(ac&&ac.file_path)?ac.file_path.split('/').pop():'';
  if(/lie_sleep/.test(f)) return 'asleep';
  const z=(tr&&tr.body)?tr.body.tran_z:null;
  if(z==null) return '—';
  if(z>100) return 'standing';
  if(z<60) return 'lying';
  return 'sitting';
}
poll();setInterval(poll,2000);
// autonomous vs manual control
let autonomous=true;
async function setAutonomous(on){autonomous=on;
  const b=$('#autoBtn');b.textContent=on?'🤖 autonomous: on':'🎮 manual control';b.style.borderColor=on?'var(--ok)':'var(--acc)';b.style.color=on?'':'var(--acc)';
  await api('behavior/pause','POST',{paused:!on});await api('behavior/random-action','POST',{enabled:on});
  toast(on?'autonomous mode on':'manual control — autonomous paused');}
function ensureManual(){if(autonomous)setAutonomous(false);}
$('#autoBtn').onclick=()=>setAutonomous(!autonomous);
// robot mode: ground = free to walk/explore; desktop = stays put (tabletop-safe)
let robotMode='desktop';
function paintMode(){const b=$('#modeBtn');const ground=robotMode==='ground';
  b.textContent=ground?'🧭 ground (roam)':'🖥️ desktop (stay)';b.style.borderColor=ground?'var(--acc)':'var(--line)';b.style.color=ground?'var(--acc)':'';}
async function setMode(m){robotMode=m;paintMode();await api('user/robot-mode','POST',{robot_mode:m});
  toast(m==='ground'?'ground mode — free to roam (keep it off tables!)':'desktop mode — stays put');}
api('user/robot-mode').then(r=>{if(r&&r.data&&r.data.robot_mode){robotMode=r.data.robot_mode;paintMode();}});
$('#modeBtn').onclick=()=>setMode(robotMode==='ground'?'desktop':'ground');
// firmware
api('ota/check').then(r=>{if(r&&r.data)$('#fw').textContent='v'+r.data.current_version;});
// speed + hold-to-move
const speed=$('#speed');speed.oninput=()=>$('#speedv').textContent=(+speed.value).toFixed(2);
let holdInt=null;
function startHold(path){ensureManual();const send=()=>api(path,'POST',{speed:+speed.value});send();holdInt=setInterval(send,350);}
function endHold(){if(holdInt){clearInterval(holdInt);holdInt=null;}api('gait/stop','POST',{});}
document.querySelectorAll('[data-hold]').forEach(b=>{
  const p=b.dataset.hold;
  b.addEventListener('mousedown',e=>{e.preventDefault();startHold(p);});
  b.addEventListener('touchstart',e=>{e.preventDefault();startHold(p);},{passive:false});
  ['mouseup','mouseleave','touchend','touchcancel'].forEach(ev=>b.addEventListener(ev,endHold));
});
$('#gstop').onclick=()=>{endHold();api('gait/stop','POST',{});};
// gait modes
$('#modes').addEventListener('click',e=>{const b=e.target.closest('[data-mode]');if(!b)return;
  document.querySelectorAll('#modes button').forEach(x=>x.classList.remove('on'));b.classList.add('on');
  api('gait/mode','POST',{mode:b.dataset.mode});toast('gait: '+b.dataset.mode);});
// pose sliders (debounced)
function bindPose(id,part,field){const el=$('#'+id),lbl=$('#'+id+'v');let t;
  el.oninput=()=>{lbl.textContent=el.value;clearTimeout(t);t=setTimeout(()=>{ensureManual();api('transform/'+part,'POST',{[field]:+el.value});},120);};}
bindPose('hyaw','head','yaw');bindPose('hpitch','head','pitch');
bindPose('bpitch','body','pitch');bindPose('broll','body','roll');bindPose('byaw','body','yaw');
$('#poseReset').onclick=()=>{['hyaw','hpitch','bpitch','broll','byaw'].forEach(id=>{$('#'+id).value=0;$('#'+id+'v').textContent='0';});
  api('transform/head','POST',{yaw:0,pitch:0});api('transform/body','POST',{pitch:0,roll:0,yaw:0});toast('pose reset');};
// torque
const tq=$('#torque');tq.oninput=()=>$('#torquev').textContent=tq.value;
// actions
let ACTIONS=[],BASE='/root/material/actions';
$('#astop').onclick=()=>api('action/stop','POST',{});
function playAction(a){ensureManual();api('action/play','POST',{file_path:BASE+'/'+a.file,torque:+tq.value});toast('▶ '+(a.label));}
function renderActions(filter){
  const q=(filter||'').toLowerCase().trim();
  const list=ACTIONS.filter(a=>!q||a.label.toLowerCase().includes(q)||a.file.toLowerCase().includes(q)||(a.category||'').toLowerCase().includes(q));
  const el=$('#alist');el.innerHTML='';
  if(!list.length){el.innerHTML='<div class="muted">no matches</div>';return;}
  let cat=null;
  list.forEach(a=>{if(a.category!==cat){cat=a.category;const h=document.createElement('div');h.className='cat';h.textContent=cat||'other';el.appendChild(h);}
    const b=document.createElement('button');b.textContent=a.label;b.title=a.file;b.onclick=()=>playAction(a);el.appendChild(b);});
}
$('#search').oninput=e=>renderActions(e.target.value);
api('action/list?limit=1000').then(r=>{
  if(!r||!r.data){$('#alist').innerHTML='<div class="muted">could not load actions</div>';return;}
  BASE=r.data.action_base_path||BASE;
  const arr=r.data.actions||[];
  ACTIONS=arr.map(a=>({file:a.file||a.filename,category:a.category||'',label:(a.display_name&&/[a-z]/i.test(a.display_name)?a.display_name:(a.filename||a.file||'').replace(/\.avi$/,'').replace(/_/g,' '))}))
    .filter(a=>a.file).sort((x,y)=>(x.category||'').localeCompare(y.category||'')||x.label.localeCompare(y.label));
  $('#acount').textContent='('+ACTIONS.length+')';
  renderActions('');
});
// play an action and wait until it reports done, so chained transitions don't
// overlap (overlapping a stand->lie transition from a non-standing pose flips it).
function _wait(ms){return new Promise(r=>setTimeout(r,ms));}
async function playWait(file,maxMs=6000){
  await api('action/play','POST',{file_path:BASE+'/'+file,torque:+tq.value});
  const t0=performance.now();
  while(performance.now()-t0<maxMs){ await _wait(400);
    const s=await api('action/status'); if(s&&s.data&&s.data.is_playing===false) break; } }
// sleep = pause autonomous + desktop-lock (can't wake itself and roam), then
// stand to a stable pose FIRST so the stand->sleep transition never flips it,
// lie into sleep, camera off. Low-power rest — NOT a real power-off.
$('#sleepBtn').onclick=async()=>{ toast('going to sleep…');
  await setAutonomous(false);   // stop the emotion engine from triggering actions
  await setMode('desktop');     // stay-put lock: no roaming even if nudged
  await playWait('stand_default_returnPosition_brief.avi');        // reach a stable stand first
  await playWait('stand_default_lie_sleep_soft_off_001_trans.avi');// then transition down safely
  // relax the motors so they go slack and COOL while resting on the ground. Low
  // torque also can't stand it up, so it stays down (no drift back to standing) —
  // without any active pose-holding.
  await api('motor/torque','POST',{torque:200});
  await setCam(false);
  toast('asleep — motors relaxed'); };
// wake = restore torque so it can stand, camera on, stand up with a stretch, restore roaming + autonomous
$('#wakeBtn').onclick=async()=>{ toast('waking…');
  await api('motor/torque','POST',{torque:2047});
  await setCam(true);
  await playWait('lie_sleep_stand_default_stretch_trans.avi');
  await setMode('ground');
  await setAutonomous(true); };
// recover from a side tip — pause autonomous so it doesn't fight, full torque to push up
$('#recLeft').onclick=()=>{ ensureManual();
  api('action/play','POST',{file_path:BASE+'/RecoveryFromLeftSideTipping.avi',torque:2047}); toast('recovering from left tip…'); };
$('#recRight').onclick=()=>{ ensureManual();
  api('action/play','POST',{file_path:BASE+'/RecoveryFromRightSideTipping.avi',torque:2047}); toast('recovering from right tip…'); };

// ---- Voice (local brain): supervise + live monitor ----
let voiceRunning=false;
const vfeed=$('#vfeed'), voiceBtn=$('#voiceBtn'), vinfo=$('#vinfo');
function paintVoice(){voiceBtn.textContent=voiceRunning?'on':'off';voiceBtn.style.color=voiceRunning?'var(--ok)':'var(--mut)';}
async function toggleVoice(){
  voiceBtn.disabled=true;
  const r=await fetch('/voice/'+(voiceRunning?'stop':'start'),{method:'POST'}).then(r=>r.json()).catch(()=>({}));
  voiceRunning=!!r.running; paintVoice(); voiceBtn.disabled=false;
  if(r.error){vinfo.textContent=r.error;} else if(voiceRunning){vinfo.textContent='starting… loading models';} else {vinfo.textContent='local brain stopped';}
}
voiceBtn.onclick=toggleVoice;
function pollVoice(){fetch('/voice/status').then(r=>r.json()).then(r=>{voiceRunning=!!r.running;paintVoice();if(!voiceRunning)vinfo.textContent='local brain stopped';}).catch(()=>{});}
pollVoice(); setInterval(pollVoice,3000);
const HK=[['homebrew','Homebrew'],['ollama_running','Ollama'],['model','Qwen model'],['deps','Python deps'],['whisper','Whisper'],['wake','Wake word'],['mic','Microphone'],['robot','Robot']];
function renderHealth(h){$('#vhealth').innerHTML=HK.map(([k,lbl])=>{const ok=!!h[k];
  return '<span style="display:inline-flex;align-items:center;gap:5px;margin:2px 12px 2px 0;white-space:nowrap"><span style="width:9px;height:9px;border-radius:50%;background:'+(ok?'var(--ok)':'var(--bad)')+'"></span>'+lbl+'</span>';}).join('');}
function checkHealth(){$('#vhealth').innerHTML='<span class="muted">checking…</span>';fetch('/voice/health').then(r=>r.json()).then(renderHealth).catch(()=>{$('#vhealth').innerHTML='<span class="muted">check failed</span>';});}
$('#vrecheck').onclick=checkHealth; checkHealth();
async function runSetup(){const btn=$('#vsetup'),out=$('#vsetuplog');
  btn.style.pointerEvents='none';btn.textContent='setting up…';out.style.display='block';out.textContent='';
  try{const resp=await fetch('/voice/setup',{method:'POST'});const rd=resp.body.getReader(),dec=new TextDecoder();
    for(;;){const {value,done}=await rd.read();if(done)break;out.textContent+=dec.decode(value);out.scrollTop=out.scrollHeight;}
  }catch(e){out.textContent+='\n[error] '+e;}
  btn.style.pointerEvents='';btn.textContent='set up / install';checkHealth();}
$('#vsetup').onclick=runSetup;
function vline(msg,color){const d=document.createElement('div');const t=new Date().toTimeString().slice(0,8);
  d.innerHTML='<span style="color:var(--mut)">'+t+'</span> '+msg;if(color)d.style.color=color;
  vfeed.appendChild(d);vfeed.scrollTop=vfeed.scrollHeight;while(vfeed.children.length>200)vfeed.removeChild(vfeed.firstChild);}
const VC={ready:'var(--acc)',wake:'var(--acc)',heard:'var(--mut)',command:'var(--ok)',no_command:'var(--warn)',ignored:'var(--warn)'};
function esc(s){return (s||'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));}
new EventSource('/voice/events').onmessage=e=>{let ev;try{ev=JSON.parse(e.data);}catch(_){return;}
  let msg;
  if(ev.kind==='ready'){vinfo.textContent='listening · whisper:'+ev.whisper+' · mic:'+ev.mic+' · llm:'+(ev.llm?'on':'off');msg='▶ listening — say &ldquo;Hey Jarvis, …&rdquo;';}
  else if(ev.kind==='wake')msg='● wake';
  else if(ev.kind==='heard')msg='&ldquo;'+esc(ev.text)+'&rdquo;';
  else if(ev.kind==='command')msg='→ '+esc((ev.commands||[]).join(', '))+' <span style="color:var(--mut)">('+ev.via+')</span>';
  else if(ev.kind==='no_command')msg='× not a command';
  else if(ev.kind==='ignored')msg='× ignored (too long)';
  else msg=esc(ev.kind);
  vline(msg, VC[ev.kind]||'');
};
</script>
</body></html>"""

class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    # Cap concurrent MJPEG upstreams to the robot — its camera server has a
    # limited client budget and leaked connections can crash the camera node.
    _stream_lock = threading.Lock()
    _stream_count = 0
    MAX_STREAMS = 2
    def log_message(self, *a): pass

    def _serve_html(self):
        b = HTML.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _proxy_stream(self):
        with H._stream_lock:
            if H._stream_count >= H.MAX_STREAMS:
                self.send_error(503, "too many camera streams"); return
            H._stream_count += 1
        up = None
        try:
            try:
                up = urllib.request.urlopen(CAM, timeout=10)
            except Exception as e:
                self.send_error(502, f"camera: {e}"); return
            ct = up.headers.get("Content-Type", "multipart/x-mixed-replace; boundary=--jpgboundary")
            self.send_response(200)
            self.send_header("Content-Type", ct)
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            try:
                while True:
                    chunk = up.read(8192)
                    if not chunk: break
                    self.wfile.write(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass
        finally:
            if up is not None:
                try: up.close()
                except Exception: pass
            with H._stream_lock:
                H._stream_count -= 1

    def _proxy_api(self, method):
        length = int(self.headers.get("Content-Length", 0) or 0)
        body = self.rfile.read(length) if length else None
        url = API + self.path  # self.path starts with /api/...
        req = urllib.request.Request(url, data=body, method=method)
        if body is not None:
            req.add_header("Content-Type", self.headers.get("Content-Type", "application/json"))
        try:
            up = urllib.request.urlopen(req, timeout=10)
            data = up.read(); code = up.status
            ct = up.headers.get("Content-Type", "application/json")
        except urllib.error.HTTPError as e:
            data = e.read(); code = e.code; ct = e.headers.get("Content-Type", "application/json")
        except Exception as e:
            data = json.dumps({"success": False, "message": f"proxy: {e}"}).encode(); code = 502; ct = "application/json"
        self.send_response(code)
        self.send_header("Content-Type", ct)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        try: self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError): pass

    def _send_json(self, obj, code=200):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        try: self.wfile.write(b)
        except (BrokenPipeError, ConnectionResetError): pass

    def _voice_start(self):
        with _voice_lock:
            p = _voice["proc"]
            if p and p.poll() is None:
                return self._send_json({"running": True, "pid": p.pid})
            if not os.path.exists(VOICE_PY):
                return self._send_json({"running": False, "error": "voice not set up (no venv)"}, 409)
            env = dict(os.environ, ROBOT=f"{ROBOT}:8088")
            try:
                out = open(os.path.join(VOICE_DIR, "voice_direct.out.log"), "a")
                _voice["proc"] = subprocess.Popen([VOICE_PY, "voice_direct.py"], cwd=VOICE_DIR,
                    env=env, stdout=out, stderr=out)
            except Exception as e:
                return self._send_json({"running": False, "error": str(e)}, 500)
            return self._send_json({"running": True, "pid": _voice["proc"].pid})

    def _voice_stop(self):
        with _voice_lock:
            p = _voice["proc"]
            if p and p.poll() is None:
                p.terminate()
            _voice["proc"] = None
        return self._send_json({"running": False})

    def _voice_status(self):
        p = _voice["proc"]
        running = bool(p and p.poll() is None)
        return self._send_json({"running": running, "pid": p.pid if running else None})

    def _voice_health(self):
        # ready/not-ready per sub-component, for the UI's readiness lights.
        h = {"homebrew": bool(shutil.which("brew")),
             "ollama": bool(shutil.which("ollama")),
             "venv": os.path.exists(VOICE_PY)}
        tags = None
        try:
            with urllib.request.urlopen("http://localhost:11434/api/tags", timeout=2) as r:
                tags = json.loads(r.read().decode())
        except Exception:
            tags = None
        h["ollama_running"] = tags is not None
        h["model"] = bool(tags and any("qwen2.5" in m.get("name", "") for m in tags.get("models", [])))
        try:
            with urllib.request.urlopen(f"{API}/api/v1/action/list?limit=1", timeout=3) as r:
                h["robot"] = r.status == 200
        except Exception:
            h["robot"] = False
        probe = {}
        if h["venv"]:
            try:
                out = subprocess.run([VOICE_PY, "-c", PROBE], capture_output=True, text=True, timeout=40)
                probe = json.loads((out.stdout or "").strip() or "{}")
            except Exception:
                probe = {}
        for k in ("deps", "mic", "wake", "whisper"):
            h[k] = bool(probe.get(k))
        p = _voice["proc"]
        h["voice_running"] = bool(p and p.poll() is None)
        return self._send_json(h)

    def _voice_events(self):
        # SSE: replay the last events for context, then follow the JSONL (tail -f). Mirrors _proxy_stream:
        # HTTP/1.1 keep-alive stream with no Content-Length; the browser reads events as they arrive.
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        f = None
        try:
            while True:
                if f is None:
                    if os.path.exists(VOICE_EVENTS):
                        f = open(VOICE_EVENTS)
                        for ln in f.readlines()[-40:]:          # replay recent context on connect
                            if ln.strip():
                                self.wfile.write(f"data: {ln.strip()}\n\n".encode())
                        self.wfile.flush()
                    else:
                        time.sleep(1); continue
                ln = f.readline()
                if ln:
                    if ln.strip():
                        self.wfile.write(f"data: {ln.strip()}\n\n".encode()); self.wfile.flush()
                else:
                    self.wfile.write(b": ping\n\n"); self.wfile.flush(); time.sleep(1)
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            if f:
                try: f.close()
                except Exception: pass

    def _voice_setup(self):
        # run setup.sh and stream its output as the response body (the UI reads it with a fetch reader).
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        script = os.path.join(VOICE_DIR, "setup.sh")
        if not os.path.exists(script):
            try: self.wfile.write(b"ERROR: setup.sh not found in VOICE_DIR\n")
            except Exception: pass
            return
        proc = None
        try:
            proc = subprocess.Popen(["bash", script], cwd=VOICE_DIR,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
            for line in proc.stdout:
                try: self.wfile.write(line.encode()); self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError): proc.terminate(); return
            proc.wait()
            self.wfile.write(f"\n[exit {proc.returncode}]\n".encode()); self.wfile.flush()
        except Exception as e:
            try: self.wfile.write(f"ERROR: {e}\n".encode())
            except Exception: pass
            if proc: proc.terminate()

    def do_GET(self):
        if self.path == "/" or self.path.startswith("/index"):
            self._serve_html()
        elif self.path.startswith("/stream"):
            self._proxy_stream()
        elif self.path.startswith("/api/"):
            self._proxy_api("GET")
        elif self.path == "/voice/status":
            self._voice_status()
        elif self.path == "/voice/health":
            self._voice_health()
        elif self.path == "/voice/events":
            self._voice_events()
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path.startswith("/api/"):
            self._proxy_api("POST")
        elif self.path == "/voice/start":
            self._voice_start()
        elif self.path == "/voice/stop":
            self._voice_stop()
        elif self.path == "/voice/setup":
            self._voice_setup()
        else:
            self.send_error(404)

if __name__ == "__main__":
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), H)
    print(f"Sirius panel: http://localhost:{PORT}  ->  robot {ROBOT}")
    srv.serve_forever()
