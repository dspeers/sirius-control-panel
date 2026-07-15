#!/usr/bin/env python3
"""Sirius local control panel + reverse proxy.

Serves a self-contained control UI at http://localhost:8777 and proxies
  /api/*   -> http://<ROBOT>:8088/api/*   (REST)
  /stream  -> http://<ROBOT>:8080/video_stream (MJPEG)
so the browser only ever talks to localhost => no CORS, no mixed-content.
"""
import sys, json, threading, urllib.request, urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROBOT = sys.argv[1] if len(sys.argv) > 1 else "192.168.4.134"
PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 8777
API = f"http://{ROBOT}:8088"
CAM = f"http://{ROBOT}:8080/video_stream"

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
    $('#pct').textContent=pc+'%';$('#batt').textContent='battery '+pc+(d.is_charging?' ⚡':'');
    $('#chg').innerHTML=(d.current>0.05?'<span class="charging">↑ '+d.current.toFixed(2)+'A</span>':(d.current<-0.05?'<span class="discharging">↓ '+d.current.toFixed(2)+'A</span>':'flat'));
    $('#voltage').textContent=d.voltage.toFixed(2)+' V · '+(d.power_supply_status_string||'')+' · '+(d.power_supply_health_string||'');
    $('#conn').textContent='online';$('#conn').className='pill charging';
  }
  const t=await api('motor/temperature');
  if(t&&t.data){const d=t.data;const vals=[d.front_left,d.front_right,d.back_left,d.back_right].filter(x=>x!=null);
    if(vals.length)$('#temp').textContent=Math.max(...vals)+'°';}
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

    def do_GET(self):
        if self.path == "/" or self.path.startswith("/index"):
            self._serve_html()
        elif self.path.startswith("/stream"):
            self._proxy_stream()
        elif self.path.startswith("/api/"):
            self._proxy_api("GET")
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path.startswith("/api/"):
            self._proxy_api("POST")
        else:
            self.send_error(404)

if __name__ == "__main__":
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), H)
    print(f"Sirius panel: http://localhost:{PORT}  ->  robot {ROBOT}")
    srv.serve_forever()
