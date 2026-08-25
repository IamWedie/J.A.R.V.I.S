let ws;
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
});

document.getElementById('validateKeyBtn').addEventListener('click', async () => {
    const key = document.getElementById('keyInput').value.trim();
    const status = document.getElementById('keyStatus');
    if (!key) { status.textContent = 'Paste a key first.'; return; }
    status.className = '';
    status.textContent = 'Validating...';
    const r = await fetch('/api/setup_validate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ key }),
    });
    const d = await r.json();
    if (!d.ok) { status.textContent = d.error; return; }
    validatedKey = key;
    status.className = 'ok';
    status.textContent = `Key valid - ${d.models.length} models available.`;
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
    document.getElementById('wizStep3').classList.remove('hidden');
});

document.getElementById('saveModelBtn').addEventListener('click', async () => {
    const model = document.getElementById('wizardModelSelect').value;
    if (!model || !validatedKey) return;
    await fetch('/api/setup_complete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ key: validatedKey, model }),
    });
    send({ cmd: 'model', model });
    loadModels();
    document.getElementById('wizStep3').classList.add('hidden');
    document.getElementById('wizStep4').classList.remove('hidden');
});

document.getElementById('wizEnrollBtn').addEventListener('click', () => {
    setEnrollStatus('Speak after the chime...');
    send({ cmd: 'enroll' });
});

document.getElementById('wizSkipBtn').addEventListener('click', finishWizard);
document.getElementById('wizFinishBtn').addEventListener('click', () => wizard.classList.add('hidden'));

async function finishWizard() {
    document.getElementById('wizStep4').classList.add('hidden');
    document.getElementById('wizDone').classList.remove('hidden');
}

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

function hideEnrollBanner() {
    enrollBanner.classList.add('hidden');
}

function setEnrollStatus(text) {
    voiceStatusEl.textContent = text;
}

function connect() {
    ws = new WebSocket(`ws://${location.host}/ws`);
    ws.onmessage = onMessage;
    ws.onclose = () => setTimeout(connect, 1500);
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
            break;
        case 'cleared':
            chatlog.innerHTML = '';
            liveBubble = null;
            break;
        case 'memory_wiped':
            addMsg('Memory wiped. I remember nothing, sir.', 'jarvis');
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
            break;
        case 'error':
            hideEnrollBanner();
            addMsg('Error: ' + msg.message, 'jarvis');
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

micBtn.addEventListener('click', () => send({ cmd: 'listen' }));

sendBtn.addEventListener('click', submitText);
textInput.addEventListener('keydown', e => { if (e.key === 'Enter') submitText(); });

function submitText() {
    const text = textInput.value.trim();
    if (!text) return;
    textInput.value = '';
    send({ cmd: 'chat', text });
}

document.getElementById('btnApprove').addEventListener('click', () => {
    approvalBox.classList.add('hidden');
    send({ cmd: 'approval', approved: true });
});
document.getElementById('btnDeny').addEventListener('click', () => {
    approvalBox.classList.add('hidden');
    send({ cmd: 'approval', approved: false });
});

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

settingsBtn.addEventListener('click', () => settingsPanel.classList.toggle('hidden'));
document.getElementById('enrollBtn').addEventListener('click', () => {
    setEnrollStatus('Preparing...');
    send({ cmd: 'enroll' });
});
document.getElementById('resetVoiceBtn').addEventListener('click', () => {
    send({ cmd: 'voice_reset' });
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

const voiceSelect = document.getElementById('voiceSelect');
const rateSelect = document.getElementById('rateSelect');

const VOICES = [
    ["en-US-AndrewNeural", "Andrew (lively US male)"],
    ["en-US-BrianNeural", "Brian (casual US male)"],
    ["en-US-AvaNeural", "Ava (US female, bright)"],
    ["en-US-AriaNeural", "Aria (US female)"],
    ["en-US-GuyNeural", "Guy (deep US male, slow)"],
    ["en-GB-RyanNeural", "Ryan (British male)"],
];

for (const [id, label] of VOICES) {
    const opt = document.createElement('option');
    opt.value = id;
    opt.textContent = label;
    voiceSelect.appendChild(opt);
}
voiceSelect.value = "en-US-AndrewNeural";
rateSelect.value = "+30%";

function pushTtsSettings() {
    send({ cmd: 'tts_settings', voice: voiceSelect.value, rate: rateSelect.value });
}

voiceSelect.addEventListener('change', pushTtsSettings);
rateSelect.addEventListener('change', pushTtsSettings);
document.getElementById('testVoiceBtn').addEventListener('click', () => send({ cmd: 'tts_test' }));

connect();
loadModels();
maybeRunSetup();
loadMemoryStats();
