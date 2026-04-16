from flask import Flask, render_template_string, request
from flask_socketio import SocketIO
from PyQt6.QtCore import QObject, pyqtSignal
import logging
import socket
import json
from zeroconf import ServiceInfo, Zeroconf

log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

logger = logging.getLogger("WebServer")

class WebController(QObject):
    web_command_signal = pyqtSignal(str, object)

web_bridge = WebController()

app_state = {
    "current_song": "No song loaded",
    "next_song": "---",
    "is_playing": False,
    "bpm": 120,
    "link_bpm": 120,
    "link_ref_time": 0, # server performance.now() equivalent
    "link_ref_beat": 0,
    "scroll_pct": 0.0,
    "click_mute": False,  
    "mutes": {
        "mg1": False, "mg2": False, "mg3": False, "mg4": False,
        "fx1": False, "fx2": False, "fx3": False, "fx4": False 
    }
}

app = Flask(__name__)
# Enable SocketIO with threading mode
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

# Global variables
_zeroconf_instance = None
_data_manager = None
connected_clients = {}

HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>MSM Remote</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.0.1/socket.io.js"></script>
    <style>
        /* Global variable for text size */
        :root {
            --dyn-font-size: 22px;
        }

        body { 
            background: #121212; color: white; font-family: sans-serif; 
            margin: 0; padding: 10px; height: 100dvh; box-sizing: border-box; /* 100dvh fixes mobile toolbar cutoff */
            display: flex; flex-direction: column; overflow: hidden;
        }
        
        .header { text-align: center; margin-bottom: 5px; position: relative; }
        .header h1 { font-size: 12px; color: #ff9900; margin: 0; text-transform: uppercase; }
        .header h2 { font-size: 20px; margin: 2px 0 0 0; color: #fff; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; padding: 0 90px; text-align: center; width: auto; }
        #next-song-header { font-size: 13px; color: #888; margin-top: 2px; font-style: italic; }
        
        /* Zoom Controls Container */
        #zoom-controls {
            position: absolute;
            top: 0;
            right: 0;
            display: flex;
            gap: 5px;
        }

        .zoom-btn {
            background-color: #444;
            color: white;
            border: 1px solid #666;
            border-radius: 4px;
            width: 40px;
            height: 35px;
            font-weight: bold;
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
            -webkit-user-select: none;
            user-select: none;
        }
        .zoom-btn:active { background-color: #666; }
        
        /* STRICT OVERRIDE: Constrict font size on the container and all its children */
        .lyrics-box, .lyrics-box * {
            font-size: var(--dyn-font-size) !important;
            line-height: 1.4 !important;
            white-space: pre-wrap !important;
            word-wrap: break-word !important;
        }

        .lyrics-box { 
            background: #111; color: #eee; text-align: left; padding: 15px 15px 40px 15px; 
            font-family: monospace; flex-grow: 1; flex-shrink: 1; min-height: 0;
            overflow-y: hidden; /* STRICT MODE: Hides scrollbar and prevents manual scrolling */
            overflow-x: hidden; 
            border-radius: 8px; margin-bottom: 10px; 
            border: 1px solid #444;
            touch-action: none; 
        }
        
        .chord-line { display: flex; flex-wrap: wrap; margin-bottom: 5px; }
        .chord-block { display: flex; flex-direction: column; justify-content: flex-end; }
        .chord { color: #1199FF; font-weight: bold; font-size: 0.9em; min-height: 1.2em; white-space: pre; padding-right: 1.0em; }
        .lyric { white-space: pre-wrap; }
        .annotation { color: #28a745; font-style: italic; white-space: pre-wrap; width: 100%; }

        .controls { display: flex; flex-direction: column; gap: 8px; }
        .row { display: flex; gap: 8px; width: 100%; }
        
        .mute-grid { 
            display: grid; 
            grid-template-columns: repeat(4, 1fr);
            gap: 10px; 
            margin-top: 15px; 
        }
        
        .btn { 
            background-color: #444; color: white; border: none; 
            padding: 12px 5px; font-size: 14px; font-weight: bold; 
            border-radius: 6px; cursor: pointer; flex: 1;
            transition: background-color 0.1s;
        }
        .btn:active { opacity: 0.7; }
        
        .btn-next { background-color: #0d6efd; font-size: 16px; }
        .btn-metro { background-color: #0dcaf0; color: black; font-size: 16px; }
        
        .btn-click-active { background-color: #198754 !important; }
        .btn-click-muted { background-color: #6c757d !important; }
        .btn-muted { background-color: #cc0000 !important; }
        
        /* LOGIN OVERLAY */
        #login-overlay {
            position: fixed; top: 0; left: 0; width: 100vw; height: 100vh;
            background: #222; z-index: 10000; display: flex; flex-direction: column;
            align-items: center; justify-content: center; gap: 15px;
        }
        .login-input {
            width: 80%; max-width: 300px; padding: 10px; font-size: 16px; border-radius: 4px; border: 1px solid #555;
            background-color: #333; color: white; margin-bottom: 10px;
        }
        .login-btn {
            background: #0dcaf0; color: black; padding: 15px; font-size: 18px;
            border-radius: 8px; cursor: pointer; width: 85%; max-width: 300px;
            text-align: center; border: none; font-weight: bold; margin-bottom: 10px;
        }
        .login-error { color: #ff4444; margin-bottom: 10px; display: none; }
        
        #switch-role-btn {
            position: absolute; top: 5px; left: 5px; font-size: 14px;
            background: transparent; color: #777; border: 1px solid #666; 
            border-radius: 4px; padding: 2px 5px; cursor: pointer; z-index: 999;
        }

        /* ROLE-BASED VISIBILITY RULES */
        .role-tech .drummer-view { display: none; }
        
        .role-musician .controls, .role-musician .drummer-view { display: none; }
        
        .role-singer .controls, .role-singer .drummer-view { display: none; }
        .role-singer .chord { display: none !important; }
        .role-singer .annotation { display: none !important; }
        .role-singer .lyrics-box, .role-singer .lyrics-box * { font-size: calc(var(--dyn-font-size) * 1.3) !important; font-weight: bold; }
        
        .role-drummer .lyrics-box, .role-drummer .controls, .role-drummer #zoom-controls { display: none; }
        .role-drummer .drummer-view { 
            display: flex; flex-direction: column; align-items: center; 
            justify-content: center; flex-grow: 1; padding: 20px;
            position: relative;
            z-index: 10;
        }
        #huge-bpm { font-size: 200px; font-weight: bold; color: #0dcaf0; margin: 0; padding: 0; line-height: 1.0; }
        #drummer-title { font-size: 40px; color: #ff9900; text-align: center; margin-top: 10px; }
        #drummer-next { font-size: 24px; color: #888; text-align: center; margin-top: 10px; font-style: italic; }
        
        /* FULLSCREEN FLASH OVERLAY (Optimized for instantaneous repaints) */
        #flash-overlay {
            display: none; /* Hidden by default, activated by role-drummer */
            position: fixed;
            top: 0;
            left: 0;
            width: 100vw;
            height: 100vh;
            background-color: #121212;
            z-index: -1; 
            will-change: background-color;
            transform: translateZ(0); /* Force GPU compositing */
        }
        .role-drummer #flash-overlay {
            display: block; /* Visible for drummer */
            z-index: 1;
        }

        /* SMALL BLINKING BOX FOR MUSICIANS */
        #musician-blink-box {
            display: none; /* Hidden by default, activated by role-musician */
            width: 100%;
            height: 30px;
            background-color: #111; /* Default off color */
            border-radius: 4px;
            margin-bottom: 5px;
            flex-shrink: 0;
            will-change: background-color;
            transform: translateZ(0); /* Force GPU compositing */
        }
        .role-musician #musician-blink-box {
            display: block; /* Visible for musician */
            z-index: 5;
        }
    </style>
</head>
<body id="main-body">
    <div id="flash-overlay"></div>
    <div id="login-overlay">
        <h2 style="color: #ff9900; margin-bottom: 20px;">MSM Remote Login</h2>
        <div id="login-error" class="login-error">Invalid credentials</div>
        <input type="text" id="username" class="login-input" placeholder="Username">
        <input type="password" id="password" class="login-input" placeholder="Password">
        <button class="login-btn" onclick="doLogin()">Login</button>
    </div>
    
    <button id="switch-role-btn" onclick="logout()">Logout</button>

    <div class="header">
        <h1>Current Song</h1>
        <h2 id="song_title">Loading...</h2>
        <div id="next-song-header">Next: <span id="next_song_val">---</span></div>
        <div id="zoom-controls">
            <div class="zoom-btn" onclick="sendCommand('transpose', -1)">♭</div>
            <div class="zoom-btn" onclick="sendCommand('transpose', 1)">#</div>
            <div class="zoom-btn" style="margin-left: 10px;" onclick="changeFontSize(-2)">A-</div>
            <div class="zoom-btn" onclick="changeFontSize(2)">A+</div>
        </div>
    </div>
    
    <div id="lyrics_container" class="lyrics-box">
        Waiting for lyrics...
    </div>
    
    <div id="musician-blink-box"></div>

    <div class="drummer-view">
        <div id="huge-bpm">120</div>
        <div id="drummer-title">Loading...</div>
        <div id="drummer-next">Next: ---</div>
    </div>
    
    <div class="controls">
        <div class="row">
            <button class="btn btn-next" onclick="sendCommand('next', {})">⏭️ NEXT</button>
            <button id="btn_metro" class="btn btn-metro" onclick="sendCommand('toggle_metronome', {})">⏱️ METRO</button>
            <button id="btn_click" class="btn btn-click-active" onclick="sendCommand('toggle_click', {})">🎧 CLICK</button>
        </div>
        
        <div class="mute-grid">
            <button id="btn_mg1" class="btn" onclick="sendCommand('toggle_mute', 'mg1')">MG 1</button>
            <button id="btn_mg2" class="btn" onclick="sendCommand('toggle_mute', 'mg2')">MG 2</button>
            <button id="btn_mg3" class="btn" onclick="sendCommand('toggle_mute', 'mg3')">MG 3</button>
            <button id="btn_mg4" class="btn" onclick="sendCommand('toggle_mute', 'mg4')">MG 4</button>
            <button id="btn_fx1" class="btn" onclick="sendCommand('toggle_mute', 'fx1')">FX 1</button>
            <button id="btn_fx2" class="btn" onclick="sendCommand('toggle_mute', 'fx2')">FX 2</button>
            <button id="btn_fx3" class="btn" onclick="sendCommand('toggle_mute', 'fx3')">FX 3</button>
            <button id="btn_fx4" class="btn" onclick="sendCommand('toggle_mute', 'fx4')">FX 4</button> 
        </div>
    </div>

    <script>
        // Connect WebSocket
        var socket = io.connect('http://' + document.domain + ':' + location.port);

        // Send Command via WebSocket instead of HTTP POST
        function sendCommand(cmd, args) {
            socket.emit('cmd', {cmd: cmd, args: args});
        }

        let currentFontSize = 22; 
        let last_known_percent = 0.0;
        let last_song = "";
        let targetScrollPos = 0;
        let currentExactScroll = 0;
        
        function changeFontSize(delta) {
            currentFontSize += delta;
            if (currentFontSize < 10) currentFontSize = 10;
            if (currentFontSize > 80) currentFontSize = 80;
            
            document.documentElement.style.setProperty('--dyn-font-size', currentFontSize + "px");
            
            const box = document.getElementById('lyrics_container');
            const maxScroll = box.scrollHeight - box.clientHeight;
            if (maxScroll > 0) {
                targetScrollPos = maxScroll * last_known_percent;
            }
        }
        
        function animateScroll() {
            let box = document.getElementById('lyrics_container');
            if (box) {
                let diff = targetScrollPos - currentExactScroll;
                
                if (Math.abs(diff) > 0.5) {
                    currentExactScroll += diff * 0.08; 
                    box.scrollTop = Math.round(currentExactScroll);
                } else {
                    currentExactScroll = targetScrollPos;
                    box.scrollTop = targetScrollPos;
                }
            }
            requestAnimationFrame(animateScroll);
        }

        requestAnimationFrame(animateScroll);
        
        function doLogin() {
            let u = document.getElementById('username').value;
            let p = document.getElementById('password').value;
            socket.emit('login', {username: u, password: p});
        }
        
        socket.on('login_response', function(data) {
            if (data.success) {
                document.getElementById('login-overlay').style.display = 'none';
                document.body.className = data.role;
                localStorage.setItem("msm_token", JSON.stringify({u: document.getElementById('username').value, p: document.getElementById('password').value}));
            } else {
                document.getElementById('login-error').style.display = 'block';
            }
        });
        
        function logout() {
            localStorage.removeItem("msm_token");
            document.getElementById('login-overlay').style.display = 'flex';
            document.body.className = "";
        }

        window.onload = function() {
            let token = localStorage.getItem("msm_token");
            if (token) {
                let t = JSON.parse(token);
                document.getElementById('username').value = t.u;
                document.getElementById('password').value = t.p;
                doLogin();
            } else {
                document.getElementById('login-overlay').style.display = 'flex';
            }
        };

        // --- PRECISION BEAT SYNC (Ableton Link Style) ---
        let linkBpm = 120;
        let linkRefTime = 0; // Server perf time
        let linkRefBeat = 0;
        let serverOffset = 0; // Client-Server clock offset
        let isPlaying = false;

        // Simple Clock Sync (Ping-Pong)
        function syncClocks() {
            let start = performance.now();
            socket.emit('ping_sync', {t: start});
        }
        
        socket.on('pong_sync', function(data) {
            let end = performance.now();
            let rtt = end - data.client_t;
            let serverTimeAtPong = data.server_t;
            // Best estimate: server_time = local_time + offset
            serverOffset = (serverTimeAtPong - (end - rtt/2));
            console.log("Clock Sync: Offset =", serverOffset, "RTT =", rtt);
        });

        // Run sync every 10s
        setInterval(syncClocks, 10000);
        setTimeout(syncClocks, 1000);

        function preciseBlinkLoop() {
            if (isPlaying) {
                let localNow = performance.now();
                let serverNow = localNow + serverOffset;
                
                let deltaSec = (serverNow - linkRefTime) / 1000.0;
                let currentBeat = linkRefBeat + (deltaSec * (linkBpm / 60.0));
                
                let phase = currentBeat % 1.0;
                
                let flashThreshold = 0.2; 
                let isDownbeat = (Math.floor(currentBeat) % 4 === 0);
                
                let mainBody = document.getElementById('main-body');

                if (mainBody.className.includes('role-drummer')) {
                    let flashLayer = document.getElementById('flash-overlay');
                    let bpmDisplay = document.getElementById('huge-bpm');
                    if (phase < flashThreshold) {
                        if (flashLayer) flashLayer.style.backgroundColor = isDownbeat ? '#ff0000' : '#cccccc';
                        if (bpmDisplay) bpmDisplay.style.color = '#ffffff';
                    } else {
                        if (flashLayer) flashLayer.style.backgroundColor = '#121212';
                        if (bpmDisplay) bpmDisplay.style.color = '#0dcaf0';
                    }
                } else if (mainBody.className.includes('role-musician')) {
                    // Only update the small blink box for musicians
                    let blinkBox = document.getElementById('musician-blink-box');
                    if (blinkBox) {
                        if (phase < flashThreshold) {
                            blinkBox.style.backgroundColor = isDownbeat ? '#ff0000' : '#00ff00';
                        } else {
                            blinkBox.style.backgroundColor = '#111'; // Default dark background when not flashing
                        }
                    }
                } else if (mainBody.className.includes('role-tech')) {
                    // Tech role: Flash the Metro button in sync
                    let metroBtn = document.getElementById('btn_metro');
                    if (metroBtn) {
                        if (phase < flashThreshold) {
                            metroBtn.style.backgroundColor = isDownbeat ? '#ff0000' : '#00ff00';
                            metroBtn.style.color = isDownbeat ? '#ffffff' : '#000000';
                        } else {
                            metroBtn.style.backgroundColor = '#0dcaf0';
                            metroBtn.style.color = '#000000';
                        }
                    }
                }
                // If a client is neither drummer nor musician (e.g., tech, singer), no blink box appears.
                // The current CSS rules already hide the specific boxes for unwanted roles.

                // For other roles, ensure both are off if they happen to be visible (shouldn't be with proper CSS)
                if (!mainBody.className.includes('role-drummer')) { // Ensure fullscreen flash is off for non-drummers
                    let flashLayer = document.getElementById('flash-overlay');
                    if (flashLayer) flashLayer.style.backgroundColor = '#121212';
                }
                if (!mainBody.className.includes('role-musician')) { // Ensure small blink box is off for non-musicians
                    let blinkBox = document.getElementById('musician-blink-box');
                    if (blinkBox) blinkBox.style.backgroundColor = '#111';
                }
                if (!mainBody.className.includes('role-tech')) { // Ensure metro button is reset if role changes
                    let metroBtn = document.getElementById('btn_metro');
                    if (metroBtn) {
                        metroBtn.style.backgroundColor = '#0dcaf0';
                        metroBtn.style.color = '#000000';
                    }
                }
            } else {
                // Not playing: ensure all visual sync indicators are in "off" state
                let flashLayer = document.getElementById('flash-overlay');
                if (flashLayer) flashLayer.style.backgroundColor = '#121212';
                let blinkBox = document.getElementById('musician-blink-box');
                if (blinkBox) blinkBox.style.backgroundColor = '#111';
                let metroBtn = document.getElementById('btn_metro');
                if (metroBtn) {
                    metroBtn.style.backgroundColor = '#0dcaf0';
                    metroBtn.style.color = '#000000';
                }
            }
            requestAnimationFrame(preciseBlinkLoop);
        }
        requestAnimationFrame(preciseBlinkLoop);

        // Receive instantaneous state updates pushed from Python
        socket.on('state_update', function(data) {
            isPlaying = data.is_playing;
            linkBpm = data.link_bpm || data.bpm;
            linkRefTime = data.link_ref_time || 0;
            linkRefBeat = data.link_ref_beat || 0;
            
            document.getElementById('song_title').innerText = data.current_song;
            document.getElementById('next_song_val').innerText = data.next_song;
            
            let drummerTitleElement = document.getElementById('drummer-title');
            if (drummerTitleElement) drummerTitleElement.innerText = data.current_song;
            
            let drummerNextElement = document.getElementById('drummer-next');
            if (drummerNextElement) drummerNextElement.innerText = "Next: " + data.next_song;
            
            let bpmDisplay = document.getElementById('huge-bpm');
            if (bpmDisplay && (data.link_bpm || data.bpm)) {
                bpmDisplay.innerText = Math.round(data.link_bpm || data.bpm);
            }
            
            last_known_percent = data.scroll_pct; 
            
            if (data.current_song !== last_song) {
                last_song = data.current_song;
                targetScrollPos = 0;
                currentExactScroll = 0;
                let box = document.getElementById('lyrics_container');
                if(box) box.scrollTop = 0;
            }
            
            // Re-render HTML 
            let box = document.getElementById('lyrics_container');
            if (box && data.chordpro_html) {
                box.innerHTML = data.chordpro_html;
            }

            // Sync Buttons
            for (let i = 1; i <= 4; i++) {
                let btnMg = document.getElementById('btn_mg' + i);
                if (btnMg) {
                    if (data.mutes && data.mutes['mg' + i]) btnMg.classList.add('btn-muted');
                    else btnMg.classList.remove('btn-muted');
                }
                
                let btnFx = document.getElementById('btn_fx' + i);
                if (btnFx) {
                    if (data.mutes && data.mutes['fx' + i]) btnFx.classList.add('btn-muted');
                    else btnFx.classList.remove('btn-muted');
                }
            }
            
            let btnClick = document.getElementById('btn_click'); 
            if (btnClick) {
                if (data.click_mute) {
                    btnClick.classList.remove('btn-click-active');
                    btnClick.classList.add('btn-click-muted');
                    btnClick.innerText = "🔇 CLICK";
                } else {
                    btnClick.classList.remove('btn-click-muted');
                    btnClick.classList.add('btn-click-active');
                    btnClick.innerText = "🎧 CLICK";
                }
            }
            
            // Update Target Scroll
            let maxScroll = box.scrollHeight - box.clientHeight;
            if (maxScroll > 0) {
                targetScrollPos = maxScroll * data.scroll_pct;
            }
        });
        
    </script>
</body>
</html>
"""

@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)

@socketio.on('ping_sync')
def handle_ping(data):
    import time
    socketio.emit('pong_sync', {
        'client_t': data.get('t'),
        'server_t': time.perf_counter() * 1000.0
    }, to=request.sid)

@socketio.on('login')
def handle_login(data):
    sid = request.sid
    username = data.get('username')
    password = data.get('password')
    
    users = {}
    if _data_manager:
        users = _data_manager.config.get("web_users", {})
    if not users:
        users = {"admin": {"password": "admin", "role": "role-tech"}}
        
    user_data = users.get(username)
    if user_data and user_data.get('password') == password:
        role = user_data.get('role', 'role-musician')
        connected_clients[sid] = {'username': username, 'role': role}
        logger.info(f"Web: Client logged in - User: {username} ({request.remote_addr})")
        socketio.emit('login_response', {'success': True, 'role': role}, to=sid)
        push_state_to_sid(sid)
    else:
        logger.warning(f"Web: Failed login attempt for user: {username} from {request.remote_addr}")
        socketio.emit('login_response', {'success': False}, to=sid)

@socketio.on('disconnect')
def handle_disconnect():
    sid = request.sid
    if sid in connected_clients:
        user_info = connected_clients.pop(sid)
        logger.info(f"Web: Client disconnected - User: {user_info['username']}")

@socketio.on('cmd')
def handle_command(data):
    sid = request.sid
    cmd = data.get('cmd')
    args = data.get('args', {})
    
    client = connected_clients.get(sid)
    if not client:
        return
        
    user = client['username']
    logger.info(f"Web: Remote Command from {user} -> {cmd} ({args})")
    
    # Handle song-specific transposition persistence
    if cmd == 'transpose' and _data_manager:
        song = app_state.get('current_song')
        if song and song != "No song loaded":
            song_db = _data_manager.database.get(song, {})
            user_transposes = song_db.setdefault("UserTranspose", {})
            current_trans = user_transposes.get(user, 0)
            user_transposes[user] = current_trans + int(args)
            _data_manager.database[song] = song_db
            _data_manager.save_database()
            push_state_to_sid(sid)
            return

    web_bridge.web_command_signal.emit(cmd, args)

def push_state_to_sid(sid):
    if sid not in connected_clients: return
    client = connected_clients[sid]
    username = client['username']
        
    local_state = app_state.copy()
    
    song = local_state.get("current_song", "")
    
    if song and song != "No song loaded" and _data_manager:
        song_db = _data_manager.database.get(song, {})
        trans = song_db.get("UserTranspose", {}).get(username, 0)
        local_state["chordpro_html"] = _data_manager.parse_chordpro_web(song, trans)
    else:
        local_state["chordpro_html"] = "Waiting for lyrics..."
        
    socketio.emit('state_update', local_state, to=sid)

def push_web_state():
    """Call this function from your main GUI loop to push updates instantly."""
    for sid in list(connected_clients.keys()):
        push_state_to_sid(sid)

def push_beat(is_downbeat):
    """Pushes an instantaneous beat tick strictly to flash visuals."""
    socketio.emit('beat_tick', {'is_downbeat': is_downbeat})

def get_local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('10.255.255.255', 1))
        ip = s.getsockname()[0]
    except Exception:
        ip = '127.0.0.1'
    finally:
        s.close()
    return ip

def start_web_server(data_manager=None, host='0.0.0.0', port=5000):
    global _zeroconf_instance, _data_manager
    _data_manager = data_manager
    ip = get_local_ip()
    print(f"Starting Web Server at: http://{ip}:{port} and http://msm.local:{port}")

    _zeroconf_instance = Zeroconf()
    desc = {'path': '/'}
    
    info = ServiceInfo(
        "_http._tcp.local.",
        "MSM._http._tcp.local.",
        addresses=[socket.inet_aton(ip)],
        port=port,
        properties=desc,
        server="msm.local.",
    )
    
    try:
        _zeroconf_instance.register_service(info)
    except Exception as e:
        print(f"Zeroconf Warning: Could not register 'msm.local' service: {e}")
    
    # Run blocks here forever until the daemon thread is killed
    socketio.run(app, host=host, port=port, debug=False, use_reloader=False)

def stop_web_server():
    """Explicitly cleans up Zeroconf so the service doesn't ghost on the network."""
    global _zeroconf_instance
    if _zeroconf_instance:
        print("Unregistering msm.local from network...")
        try:
            _zeroconf_instance.unregister_all_services()
            _zeroconf_instance.close()
        except Exception as e:
            print(f"Error closing Zeroconf: {e}")
