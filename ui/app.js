let ws;
let reconnectDelay = 1000;
let reconnectTimer = null;
const MAX_RECONNECT = 30000;

const chatlog = document.getElementById('chatlog');
const micBtn = document.getElementById('micBtn');
const textInput = document.getElementById('textInput');
const sendBtn = document.getElementById('sendBtn');
const approvalBox = document.getElementById('approvalBox');
const approvalText = document.getElementById('approvalText');
const modelSelect = document.getElementById('modelSelect');
const wakeBtn = document.getElementById('wakeBtn');
const settingsBtn = document.getElementById('settingsBtn');
const settingsPanel = document.getElementById('settingsPanel');
const voiceStatusEl = document.getElementById('voiceStatus');

const wizard = document.getElementById('setupWizard');
let validatedKey = null;

/* === TOAST SYSTEM === */
function showToast(text, type = 'info', duration = 4000) {
    const container = document.getElementById('toastContainer');
    const el = document.createElement('div');
    el.className = `toast ${type}`;
    el.textContent = text;
    el.setAttribute('role', 'status');
    container.appendChild(el);
    setTimeout(() => {
        el.classList.add('fadeout');
        el.addEventListener('animationend', () => el.remove());
    }, duration);
}

/* === SETUP WIZARD === */
async function maybeRunSetup() {
    try {
        const r = await fetch('/api/setup_status');
        const d = await r.json();
        if (d.setup_needed) showWizard();
    } catch {}
}

function showWizard() {
    wizard.classList.remove('hidden');
    fetch('/api/terms').then(r => r.json()).then(d => {
        document.getElementById('termsBox').textContent = d.text;
    });
}

document.getElementById('agreeChk').addEventListener('change', e => {
    document.getElementById('agreeBtn').disabled = !e.target.checked;
});

document.getElementById('agreeBtn').addEventListener('click', () => {
    document.getElementById('wizStep1').classList.add('hidden');
    document.getElementById('wizStep2').classList.remove('hidden');
    document.getElementById('keyInput').focus();
});

document.getElementById('validateKeyBtn').addEventListener('click', async () => {
    const key = document.getElementById('keyInput').value.trim();
    const status = document.getElementById('keyStatus');
    const btn = document.getElementById('validateKeyBtn');
    if (!key) { status.textContent = 'Paste a key first.'; return; }
    status.className = '';
    status.innerHTML = '<span class="spinner"></span>Validating...';
    btn.disabled = true;
    const r = await fetch('/api/setup_validate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ key }),
    });
    const d = await r.json();
    btn.disabled = false;
    if (!d.ok) { status.textContent = d.error; return; }
    validatedKey = key;
    status.className = 'ok';
    status.textContent = `Key valid - ${d.models.length} models available.`;
    showToast('API key validated successfully', 'success');
    const sel = document.getElementById('wizardModelSelect');
    sel.innerHTML = '';
    for (const m of d.models) {
        const opt = document.createElement('option');
        opt.value = m.id;
        opt.textContent = m.label;
        if (m.id === 'mimo-v2.5-free') opt.selected = true;
        sel.appendChild(opt);
    }
    document.getElementById('wizStep2').classList.add('hidden');
    document.getElementById('wizStep3Check').classList.remove('hidden');
    runSystemCheck();
});

/* === SYSTEM CHECK === */
async function runSystemCheck() {
    const ids = { microphone: 'checkMic', speaker: 'checkSpeaker', internet: 'checkInternet', stt_model: 'checkSTT' };
    try {
        const r = await fetch('/api/system_check');
        const d = await r.json();
        let allOk = true;
        for (const [key, elId] of Object.entries(ids)) {
            const el = document.getElementById(elId);
            const ok = d[key];
            el.className = 'checkRow ' + (ok ? 'ok' : 'fail');
            el.innerHTML = (ok ? '&#10003;' : '&#10007;') + ' ' + el.textContent.replace(/^.*?(Microphone|Speakers|Internet|Voice engine)/, '$1');
            if (!ok) allOk = false;
        }
        const nextBtn = document.getElementById('checkNextBtn');
        nextBtn.disabled = false;
        if (allOk) {
            showToast('All systems ready', 'success');
        } else {
            showToast('Some checks failed — you may have issues', 'warning');
        }
    } catch {
        showToast('Could not run system check', 'warning');
        document.getElementById('checkNextBtn').disabled = false;
    }
}

document.getElementById('checkNextBtn').addEventListener('click', () => {
    document.getElementById('wizStep3Check').classList.add('hidden');
    document.getElementById('wizStep3').classList.remove('hidden');
});

document.getElementById('saveModelBtn').addEventListener('click', async () => {
    const model = document.getElementById('wizardModelSelect').value;
    if (!model || !validatedKey) return;
    const btn = document.getElementById('saveModelBtn');
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner"></span>Saving...';
    await fetch('/api/setup_complete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ key: validatedKey, model }),
    });
    send({ cmd: 'model', model });
    loadModels();
    btn.disabled = false;
    btn.textContent = 'SAVE & CONTINUE';
    showToast(`Brain set to ${model}`, 'success');
    document.getElementById('wizStep3').classList.add('hidden');
    document.getElementById('wizStep4').classList.remove('hidden');
});

document.getElementById('wizEnrollBtn').addEventListener('click', () => {
    setEnrollStatus('Speak after the chime...');
    send({ cmd: 'enroll' });
});

document.getElementById('wizSkipBtn').addEventListener('click', finishWizard);
document.getElementById('wizFinishBtn').addEventListener('click', () => {
    wizard.classList.add('hidden');
    showToast('JARVIS is ready', 'success');
});

async function finishWizard() {
    document.getElementById('wizStep4').classList.add('hidden');
    document.getElementById('wizDone').classList.remove('hidden');
}

/* === VOICE STATUS === */
function updateVoiceStatus(enrolled) {
    voiceStatusEl.textContent = enrolled ? 'ACTIVE - owner only' : 'OFF - not enrolled';
    voiceStatusEl.classList.toggle('active', !!enrolled);
}

const enrollBanner = document.getElementById('enrollBanner');
const enrollPhraseEl = document.getElementById('enrollPhrase');
const enrollStepEl = document.getElementById('enrollStep');
const enrollHintEl = document.getElementById('enrollHint');

function showEnrollBanner(msg) {
    if (msg.retry) {
        enrollStepEl.textContent = `PHRASE ${msg.index || '?'} OF ${msg.total}`;
        enrollHintEl.textContent = "didn't catch that - say it again";
        return;
    }
    enrollStepEl.textContent = `PHRASE ${msg.index} OF ${msg.total}`;
    enrollPhraseEl.textContent = `"${msg.phrase}"`;
    enrollHintEl.textContent = 'read it out loud, then pause';
    enrollBanner.classList.remove('hidden');
}

function hideEnrollBanner() { enrollBanner.classList.add('hidden'); }
function setEnrollStatus(text) { voiceStatusEl.textContent = text; }

/* === WEBSOCKET === */
function connect() {
    if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null; }
    ws = new WebSocket(`ws://${location.host}/ws`);
    ws.onopen = () => {
        reconnectDelay = 1000;
        document.getElementById('status').textContent = 'Online';
    };
    ws.onmessage = onMessage;
    ws.onclose = () => {
        document.getElementById('status').textContent = 'Reconnecting...';
        reconnectTimer = setTimeout(connect, reconnectDelay);
        reconnectDelay = Math.min(reconnectDelay * 1.5, MAX_RECONNECT);
    };
    ws.onerror = () => ws.close();
}

function onMessage(ev) {
    let msg;
    try { msg = JSON.parse(ev.data); } catch { return; }

    switch (msg.type) {
        case 'state':
            setState(msg.value);
            micBtn.classList.toggle('listening', msg.value === 'listening');
            break;
        case 'level':
            setLevel(msg.value);
            break;
        case 'user_said':
            addMsg(msg.text, 'user');
            break;
        case 'reply_chunk':
            if (!liveBubble) liveBubble = addMsg('', 'jarvis');
            liveBubble.textContent += msg.text;
            chatlog.scrollTop = chatlog.scrollHeight;
            break;
        case 'reply':
            if (liveBubble) {
                if (msg.text) liveBubble.textContent = msg.text;
                liveBubble = null;
            } else if (msg.text) {
                addMsg(msg.text, 'jarvis');
            }
            break;
        case 'approval_request':
            approvalText.textContent = 'JARVIS wants to: ' + msg.description;
            approvalBox.classList.remove('hidden');
            document.getElementById('btnApprove').focus();
            showToast('Action requires approval', 'warning', 8000);
            break;
        case 'cleared':
            chatlog.innerHTML = '';
            liveBubble = null;
            break;
        case 'memory_wiped':
            addMsg('Memory wiped. I remember nothing, sir.', 'jarvis');
            showToast('Memory wiped', 'info');
            break;
        case 'history_batch':
            for (const item of msg.items) {
                addMsg(item.text, item.role === 'user' ? 'user' : 'jarvis');
            }
            break;
        case 'wake':
            updateWakeBtn(msg.enabled);
            break;
        case 'voice_status':
            updateVoiceStatus(msg.enrolled);
            break;
        case 'enroll_prompt':
            showEnrollBanner(msg);
            break;
        case 'phrase_done':
            enrollPhraseEl.textContent = 'Got it.';
            enrollHintEl.textContent = 'next phrase...';
            break;
        case 'enroll_done':
            hideEnrollBanner();
            setEnrollStatus('Fingerprint saved.');
            showToast('Voice fingerprint enrolled', 'success');
            if (!wizard.classList.contains('hidden')) {
                finishWizard();
            } else {
                setTimeout(() => settingsPanel.classList.add('hidden'), 1500);
            }
            break;
        case 'tts_saved':
            addMsg(`Voice saved: ${msg.voice} ${msg.rate}`, 'jarvis');
            break;
        case 'voice_rejected':
            addMsg('[unrecognized voice ignored]', 'jarvis');
            showToast('Unrecognized voice - access denied', 'warning');
            break;
        case 'error':
            hideEnrollBanner();
            addMsg('Error: ' + msg.message, 'jarvis');
            showToast(msg.message, 'error', 6000);
            break;
        case 'update_available':
            showToast(`Update available: v${msg.latest} (you have v${msg.current})`, 'info', 10000);
            addMsg(`A new version (v${msg.latest}) is available. Click the update button in settings to download.`, 'jarvis');
            break;
        case 'update_progress':
            if (msg.status === 'checking') showToast('Checking for updates...', 'info', 3000);
            else if (msg.status === 'downloading') showToast('Downloading update...', 'info', 10000);
            else if (msg.status === 'ready') showToast('Update ready — restarting...', 'success');
            break;
    }
}

function updateWakeBtn(on) {
    wakeBtn.textContent = on ? 'WAKE: ON' : 'WAKE: OFF';
    wakeBtn.classList.toggle('on', on);
}

function send(obj) { if (ws && ws.readyState === 1) ws.send(JSON.stringify(obj)); }

function addMsg(text, who) {
    const div = document.createElement('div');
    div.className = 'msg ' + who;
    div.textContent = text;
    chatlog.appendChild(div);
    chatlog.scrollTop = chatlog.scrollHeight;
    return div;
}

let liveBubble = null;

/* === INPUT === */
micBtn.addEventListener('click', () => send({ cmd: 'listen' }));
sendBtn.addEventListener('click', submitText);
textInput.addEventListener('keydown', e => { if (e.key === 'Enter') submitText(); });

function submitText() {
    const text = textInput.value.trim();
    if (!text) return;
    textInput.value = '';
    send({ cmd: 'chat', text });
}

/* === APPROVAL === */
document.getElementById('btnApprove').addEventListener('click', () => {
    approvalBox.classList.add('hidden');
    send({ cmd: 'approval', approved: true });
});
document.getElementById('btnDeny').addEventListener('click', () => {
    approvalBox.classList.add('hidden');
    send({ cmd: 'approval', approved: false });
});

/* === MODELS === */
async function loadModels() {
    try {
        const res = await fetch('/api/models');
        const data = await res.json();
        if (!data.models || !data.models.length) return;
        modelSelect.innerHTML = '';
        for (const m of data.models) {
            const opt = document.createElement('option');
            opt.value = m.id;
            opt.textContent = m.label || m.id;
            modelSelect.appendChild(opt);
        }
        const preferred = ['x-preview-f-free'];
        for (const p of preferred) {
            if ([...modelSelect.options].some(o => o.value === p)) modelSelect.value = p;
        }
        send({ cmd: 'model', model: modelSelect.value });
    } catch {}
}

modelSelect.addEventListener('change', () => send({ cmd: 'model', model: modelSelect.value }));
wakeBtn.addEventListener('click', () => send({ cmd: 'wake_toggle', enabled: wakeBtn.textContent.includes('OFF') }));

/* === SETTINGS === */
settingsBtn.addEventListener('click', () => {
    const open = settingsPanel.classList.toggle('hidden');
    settingsBtn.setAttribute('aria-expanded', !open);
});
document.getElementById('enrollBtn').addEventListener('click', () => {
    setEnrollStatus('Preparing...');
    send({ cmd: 'enroll' });
});
document.getElementById('resetVoiceBtn').addEventListener('click', () => {
    send({ cmd: 'voice_reset' });
    showToast('Voice fingerprint reset', 'info');
});
document.getElementById('wipeMemoryBtn').addEventListener('click', () => {
    if (confirm('Erase ALL memories and conversations permanently?')) {
        send({ cmd: 'wipe_memory' });
    }
});

async function loadMemoryStats() {
    try {
        const res = await fetch('/api/memory_stats');
        const d = await res.json();
        document.getElementById('memStats').textContent =
            `${d.conversations} messages remembered | ${d.facts} facts`;
    } catch {}
}

/* === TTS === */
const voiceSelect = document.getElementById('voiceSelect');
const rateSelect = document.getElementById('rateSelect');

const VOICES = [
    ["en-US-AndrewNeural", "Andrew (lively US male)"],
    ["en-US-BrianNeural", "Brian (casual US male)"],
    ["en-US-AvaNeural", "Ava (US female, bright)"],
    ["en-US-AriaNeural", "Aria (US female)"],
    ["en-US-GuyNeural", "Guy (deep US male, slow)"],
    ["en-GB-RyanNeural", "Ryan (British male)"],
    ["ar-TN-HediNeural", "Hedi (Tunisian Arabic male)"],
    ["ar-TN-ReemNeural", "Reem (Tunisian Arabic female)"],
];

for (const [id, label] of VOICES) {
    const opt = document.createElement('option');
    opt.value = id;
    opt.textContent = label;
    voiceSelect.appendChild(opt);
}
voiceSelect.value = "ar-TN-HediNeural";
rateSelect.value = "+0%";

function pushTtsSettings() {
    send({ cmd: 'tts_settings', voice: voiceSelect.value, rate: rateSelect.value });
}

voiceSelect.addEventListener('change', pushTtsSettings);
rateSelect.addEventListener('change', pushTtsSettings);
document.getElementById('testVoiceBtn').addEventListener('click', () => {
    send({ cmd: 'tts_test' });
    showToast('Testing voice...', 'info', 2000);
});

/* === UPDATE === */
document.getElementById('updateBtn').addEventListener('click', () => {
    send({ cmd: 'update_download' });
});
async function loadVersion() {
    try {
        const r = await fetch('/api/health');
        const d = await r.json();
        document.getElementById('versionLabel').textContent = 'v' + (d.version || '?');
        if (d.language) document.getElementById('langSelect').value = d.language;
    } catch {}
}

/* === LANGUAGE === */
const langSelect = document.getElementById('langSelect');
langSelect.addEventListener('change', () => {
    send({ cmd: 'set_language', lang: langSelect.value });
    location.reload();
});

/* === INIT === */
connect();
loadModels();
maybeRunSetup();
loadMemoryStats();
loadVersion();

/* === HEALTH CHECK === */
const offlineBanner = document.getElementById('offlineBanner');
async function checkHealth() {
    try {
        const r = await fetch('/api/health');
        const d = await r.json();
        offlineBanner.classList.toggle('hidden', d.online);
    } catch {
        offlineBanner.classList.remove('hidden');
    }
}
setInterval(checkHealth, 30000);
checkHealth();
