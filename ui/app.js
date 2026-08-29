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

/* === I18N === */
const I18N = {
    en: {
        online: 'Online',
        reconnecting: 'Reconnecting...',
        type_command: 'Type a command...',
        wake_off_label: 'WAKE: OFF',
        wake_on_label: 'WAKE: ON',
        api_key_hint_pre: 'Create one free at',
        api_key_hint_post: ', then paste it here.',
        license_heading: 'ACTIVATE YOUR LICENSE',
        license_hint: 'Enter the license key you received with your purchase (JARV-XXXXX-XXXXX-XXXXX-XXXXX).',
        activate: 'ACTIVATE',
        activating: 'Activating...',
        license_invalid: 'That license key is not valid.',
        license_valid: 'License activated.',
        license_enter: 'Enter a license key first.',
        activating_failed: 'Could not reach the license service.',
        enroll_info: 'Enroll = say 3 short phrases. After that JARVIS only obeys this voice via wake word.',
        pasted_key_first: 'Paste a key first.',
        validating: 'Validating...',
        key_ok: 'Key valid - {count} models available.',
        saving: 'Saving...',
        all_systems_ready: 'All systems ready',
        some_checks_failed: 'Some checks failed — you may have issues',
        system_check_failed: 'Could not run system check',
        speak_after_chime: 'Speak after the chime...',
        preparing: 'Preparing...',
        fingerprint_reset: 'Voice fingerprint reset',
        testing_voice: 'Testing voice...',
        api_key_validated: 'API key validated successfully',
        brain_set: 'Brain set to {model}',
        jarvis_ready: 'JARVIS is ready',
        active_owner: 'ACTIVE - owner only',
        off_not_enrolled: 'OFF - not enrolled',
        phrase_of: 'PHRASE {index} OF {total}',
        did_not_catch: "didn't catch that - say it again",
        read_loud: 'read it out loud, then pause',
        next_phrase: 'next phrase...',
        got_it: 'Got it.',
        memory_wiped_msg: 'Memory wiped. I remember nothing, sir.',
        memory_wiped: 'Memory wiped',
        fingerprint_saved: 'Fingerprint saved.',
        voice_enrolled: 'Voice fingerprint enrolled',
        voice_saved: 'Voice saved: {voice} {rate}',
        unrecognized_voice: '[unrecognized voice ignored]',
        access_denied: 'Unrecognized voice - access denied',
        error_prefix: 'Error: {message}',
        approval_requires: 'JARVIS wants to: {description}',
        action_requires_approval: 'Action requires approval',
        update_available: 'Update available: v{latest} (you have v{current})',
        new_version_available: 'A new version (v{latest}) is available. Click the update button in settings to download.',
        checking_updates: 'Checking for updates...',
        downloading_update: 'Downloading update...',
        update_ready: 'Update ready — restarting...',
        no_update: 'No update available',
        update_download_failed: 'Download failed',
        update_install_failed: 'Install failed',
        memory_remembered: '{conversations} messages remembered | {facts} facts',
        test: 'Testing voice...',
        confirm_wipe: 'Erase ALL memories and conversations permanently?',
    },
    ar: {
        setup_title: 'إعداد جارفيس',
        terms_heading: '1. شروط الاستخدام',
        terms_agree: 'قرأت ووافقت على شروط الاستخدام',
        continue: 'متابعة',
        api_key_heading: '2. مفتاح API Zen',
        validate_key: 'تحقق من المفتاح',
        system_check_heading: '3. فحص النظام',
        system_check_hint: 'التأكد من جاهزية العتاد...',
        mic: 'الميكروفون',
        speakers: 'مكبرات الصوت',
        internet: 'الإنترنت',
        voice_engine: 'محرك الصوت',
        brain_heading: '4. اختر ذكاء جارفيس',
        brain_hint: 'النماذج المجانية في المقدمة. يمكنك تغيير هذا في أي وقت.',
        save_continue: 'حفظ ومتابعة',
        voice_heading: '5. قفل الصوت (اختياري)',
        voice_hint: 'سجّل صوتك حتى لا يطيعك أحد غيرك. يمكنك التخطي والعودة لاحقاً من الإعدادات.',
        enroll_voice: 'تسجيل صوتي',
        skip: 'تخطي الآن',
        all_set: 'جاهز يا سيدي.',
        start: 'تشغيل جارفيس',
        voice_lock: 'قفل الصوت',
        reset_fingerprint: 'إعادة تعيين البصمة',
        forget_everything: 'مسح كل شيء',
        voice_speed: 'الصوت والسرعة',
        test_voice: 'اختبار الصوت',
        check_updates: 'التحقق من التحديثات',
        no_internet: 'لا يوجد اتصال بالإنترنت — الميزات الصوتية والذكية غير متاحة',
        deny: 'رفض',
        approve: 'موافقة',
        online: 'متصل',
        reconnecting: 'إعادة الاتصال...',
        type_command: 'اكتب أمراً...',
        wake_off_label: 'استيقاظ: مغلق',
        wake_on_label: 'استيقاظ: مفتوح',
        api_key_hint_pre: 'أنشئ مفتاحاً مجانياً على',
        api_key_hint_post: 'ثم الصقه هنا.',
        enroll_info: 'التسجيل: قل 3 عبارات قصيرة. بعدها لن يطيعك إلا صوتك.',
        pasted_key_first: 'الصق مفتاحاً أولاً.',
        validating: 'جاري التحقق...',
        key_ok: 'مفتاح صالح - {count} نماذج متاحة.',
        license_heading: 'تفعيل الترخيص',
        license_hint: 'أدخل مفتاح الترخيص الذي حصلت عليه مع عملية الشراء (JARV-XXXXX-XXXXX-XXXXX-XXXXX).',
        activate: 'تفعيل',
        activating: 'جاري التفعيل...',
        license_invalid: 'مفتاح الترخيص غير صالح.',
        license_valid: 'تم تفعيل الترخيص.',
        license_enter: 'أدخل مفتاح الترخيص أولاً.',
        activating_failed: 'تعذر الوصول إلى خدمة الترخيص.',
        saving: 'جاري الحفظ...',
        all_systems_ready: 'جميع الأنظمة جاهزة',
        some_checks_failed: 'فشلت بعض الفحوصات — قد تواجه مشاكل',
        system_check_failed: 'تعذر تشغيل فحص النظام',
        speak_after_chime: 'تحدث بعد الجرس...',
        preparing: 'جاري التحضير...',
        fingerprint_reset: 'تم إعادة تعيين بصمة الصوت',
        testing_voice: 'جاري اختبار الصوت...',
        api_key_validated: 'تم التحقق من المفتاح بنجاح',
        brain_set: 'تم اختيار النموذج {model}',
        jarvis_ready: 'جارفيس جاهز',
        active_owner: 'نشط - المالك فقط',
        off_not_enrolled: 'مغلق - غير مسجل',
        phrase_of: 'العبارة {index} من {total}',
        did_not_catch: 'لم أسمع جيداً - أعدها',
        read_loud: 'اقرأ بصوت عالٍ ثم توقف',
        next_phrase: 'العبارة التالية...',
        got_it: 'فهمت.',
        memory_wiped_msg: 'تم مسح الذاكرة. لا أتذكر شيئاً يا سيدي.',
        memory_wiped: 'تم مسح الذاكرة',
        fingerprint_saved: 'تم حفظ البصمة.',
        voice_enrolled: 'تم تسجيل بصمة الصوت',
        voice_saved: 'تم حفظ الصوت: {voice} {rate}',
        unrecognized_voice: '[تم تجاهل صوت غير معروف]',
        access_denied: 'صوت غير معروف - الرفض',
        error_prefix: 'خطأ: {message}',
        approval_requires: 'جارفيس يريد: {description}',
        action_requires_approval: 'يتطلب هذا الإجراء موافقة',
        update_available: 'تحديث متاح: v{latest} (عندك v{current})',
        new_version_available: 'تتوفر نسخة جديدة (v{latest}). اضغط زر التحديث في الإعدادات للتحميل.',
        checking_updates: 'جاري التحقق من التحديثات...',
        downloading_update: 'جاري تحميل التحديث...',
        update_ready: 'التحديث جاهز — جاري إعادة التشغيل...',
        no_update: 'لا يوجد تحديث متاح',
        update_download_failed: 'فشل التحميل',
        update_install_failed: 'فشل التثبيت',
        memory_remembered: '{conversations} رسائل | {facts} حقائق',
        test: 'جاري اختبار الصوت...',
        confirm_wipe: 'مسح جميع الذكريات والمحادثات نهائياً؟',
    },
};

let currentLang = 'en';
function t(key, vars) {
    const dict = I18N[currentLang] || I18N.en;
    let s = dict[key] !== undefined ? dict[key] : (I18N.en[key] !== undefined ? I18N.en[key] : key);
    if (vars) { for (const k in vars) s = s.split('{' + k + '}').join(vars[k]); }
    return s;
}

function applyTranslations() {
    document.documentElement.lang = currentLang;
    document.documentElement.dir = currentLang === 'ar' ? 'rtl' : 'ltr';
    if (currentLang !== 'en') {
        document.querySelectorAll('[data-i18n]').forEach(el => {
            const key = el.getAttribute('data-i18n');
            const text = t(key);
            if (el.querySelector('[data-i18n]')) {
                el.childNodes.forEach(n => {
                    if (n.nodeType === 3 && n.textContent.trim() !== '') n.textContent = text;
                });
            } else {
                el.textContent = text;
            }
        });
        document.querySelectorAll('[data-i18n-placeholder]').forEach(el => {
            el.placeholder = t(el.getAttribute('data-i18n-placeholder'));
        });
    }
    updateWakeBtn(wakeOn);
    updateVoiceStatus(voiceEnrolled);
}

async function loadLang() {
    try {
        const r = await fetch('/api/language');
        const d = await r.json();
        currentLang = d.current || 'en';
        document.getElementById('langSelect').value = currentLang;
    } catch {}
    applyTranslations();
}
let wakeOn = false;
let voiceEnrolled = false;

/* === SETUP WIZARD === */
async function maybeRunSetup() {
    try {
        const r = await fetch('/api/setup_status');
        const d = await r.json();
        if (d.setup_needed || d.license_needed) showWizard(d.license_needed);
    } catch {}
}

function showWizard(licenseNeeded) {
    wizard.classList.remove('hidden');
    if (licenseNeeded) {
        // license gate first, before terms
        document.getElementById('wizStep0').classList.remove('hidden');
        document.getElementById('licenseInput').focus();
        return;
    }
    fetch('/api/terms').then(r => r.json()).then(d => {
        document.getElementById('termsBox').textContent = d.text;
    });
}

document.getElementById('activateLicenseBtn').addEventListener('click', async () => {
    const key = document.getElementById('licenseInput').value.trim();
    const status = document.getElementById('licenseStatus');
    const btn = document.getElementById('activateLicenseBtn');
    if (!key) { status.textContent = t('license_enter'); return; }
    status.className = '';
    status.innerHTML = '<span class="spinner"></span>' + t('activating');
    btn.disabled = true;
    try {
        const r = await fetch('/api/license_activate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ key }),
        });
        const d = await r.json();
        if (!d.ok) {
            status.textContent = d.error || t('license_invalid');
            btn.disabled = false;
            return;
        }
        status.className = 'ok';
        status.textContent = t('license_valid');
        document.getElementById('wizStep0').classList.add('hidden');
        fetch('/api/terms').then(res => res.json()).then(dt => {
            document.getElementById('termsBox').textContent = dt.text;
        });
        document.getElementById('wizStep1').classList.remove('hidden');
        document.getElementById('agreeChk').focus();
    } catch {
        status.textContent = t('activating_failed');
        btn.disabled = false;
    }
});

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
    if (!key) { status.textContent = t('pasted_key_first'); return; }
    status.className = '';
    status.innerHTML = '<span class="spinner"></span>' + t('validating');
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
    status.textContent = t('key_ok', { count: d.models.length });
    showToast(t('api_key_validated'), 'success');
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
            const labelEl = el.querySelector('[data-i18n]');
            el.innerHTML = (ok ? '&#10003;' : '&#10007;');
            if (labelEl) el.appendChild(labelEl);
            if (!ok) allOk = false;
        }
        const nextBtn = document.getElementById('checkNextBtn');
        nextBtn.disabled = false;
        if (allOk) {
            showToast(t('all_systems_ready'), 'success');
        } else {
            showToast(t('some_checks_failed'), 'warning');
        }
    } catch {
        showToast(t('system_check_failed'), 'warning');
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
    btn.innerHTML = '<span class="spinner"></span>' + t('saving');
    await fetch('/api/setup_complete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ key: validatedKey, model }),
    });
    send({ cmd: 'model', model });
    loadModels();
    btn.disabled = false;
    btn.textContent = t('save_continue');
    showToast(t('brain_set', { model }), 'success');
    document.getElementById('wizStep3').classList.add('hidden');
    document.getElementById('wizStep4').classList.remove('hidden');
});

document.getElementById('wizEnrollBtn').addEventListener('click', () => {
    setEnrollStatus(t('speak_after_chime'));
    send({ cmd: 'enroll' });
});

document.getElementById('wizSkipBtn').addEventListener('click', finishWizard);
document.getElementById('wizFinishBtn').addEventListener('click', () => {
    wizard.classList.add('hidden');
    showToast(t('jarvis_ready'), 'success');
});

async function finishWizard() {
    document.getElementById('wizStep4').classList.add('hidden');
    document.getElementById('wizDone').classList.remove('hidden');
}

/* === VOICE STATUS === */
function updateVoiceStatus(enrolled) {
    voiceEnrolled = enrolled;
    voiceStatusEl.textContent = enrolled ? t('active_owner') : t('off_not_enrolled');
    voiceStatusEl.classList.toggle('active', !!enrolled);
}

const enrollBanner = document.getElementById('enrollBanner');
const enrollPhraseEl = document.getElementById('enrollPhrase');
const enrollStepEl = document.getElementById('enrollStep');
const enrollHintEl = document.getElementById('enrollHint');

function showEnrollBanner(msg) {
    if (msg.retry) {
        enrollStepEl.textContent = t('phrase_of', { index: msg.index || '?', total: msg.total });
        enrollHintEl.textContent = t('did_not_catch');
        return;
    }
    enrollStepEl.textContent = t('phrase_of', { index: msg.index, total: msg.total });
    enrollPhraseEl.textContent = `"${msg.phrase}"`;
    enrollHintEl.textContent = t('read_loud');
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
        document.getElementById('status').textContent = t('online');
    };
    ws.onmessage = onMessage;
    ws.onclose = () => {
        document.getElementById('status').textContent = t('reconnecting');
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
            approvalText.textContent = t('approval_requires', { description: msg.description });
            approvalBox.classList.remove('hidden');
            document.getElementById('btnApprove').focus();
            showToast(t('action_requires_approval'), 'warning', 8000);
            break;
        case 'cleared':
            chatlog.innerHTML = '';
            liveBubble = null;
            break;
        case 'memory_wiped':
            addMsg(t('memory_wiped_msg'), 'jarvis');
            showToast(t('memory_wiped'), 'info');
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
            enrollPhraseEl.textContent = t('got_it');
            enrollHintEl.textContent = t('next_phrase');
            break;
        case 'enroll_done':
            hideEnrollBanner();
            setEnrollStatus(t('fingerprint_saved'));
            showToast(t('voice_enrolled'), 'success');
            if (!wizard.classList.contains('hidden')) {
                finishWizard();
            } else {
                setTimeout(() => settingsPanel.classList.add('hidden'), 1500);
            }
            break;
        case 'tts_saved':
            addMsg(t('voice_saved', { voice: msg.voice, rate: msg.rate }), 'jarvis');
            break;
        case 'voice_rejected':
            addMsg(t('unrecognized_voice'), 'jarvis');
            showToast(t('access_denied'), 'warning');
            break;
        case 'error':
            hideEnrollBanner();
            addMsg(t('error_prefix', { message: msg.message }), 'jarvis');
            showToast(msg.message, 'error', 6000);
            break;
        case 'update_available':
            showToast(t('update_available', { latest: msg.latest, current: msg.current }), 'info', 10000);
            addMsg(t('new_version_available', { latest: msg.latest }), 'jarvis');
            break;
        case 'update_progress':
            if (msg.status === 'checking') showToast(t('checking_updates'), 'info', 3000);
            else if (msg.status === 'downloading') showToast(t('downloading_update'), 'info', 10000);
            else if (msg.status === 'ready') showToast(t('update_ready'), 'success');
            break;
    }
}

function updateWakeBtn(on) {
    wakeOn = on;
    wakeBtn.textContent = on ? t('wake_on_label') : t('wake_off_label');
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
wakeBtn.addEventListener('click', () => send({ cmd: 'wake_toggle', enabled: !wakeOn }));

/* === SETTINGS === */
settingsBtn.addEventListener('click', () => {
    const open = settingsPanel.classList.toggle('hidden');
    settingsBtn.setAttribute('aria-expanded', !open);
});
document.getElementById('enrollBtn').addEventListener('click', () => {
    setEnrollStatus(t('preparing'));
    send({ cmd: 'enroll' });
});
document.getElementById('resetVoiceBtn').addEventListener('click', () => {
    send({ cmd: 'voice_reset' });
    showToast(t('fingerprint_reset'), 'info');
});
document.getElementById('wipeMemoryBtn').addEventListener('click', () => {
    if (confirm(t('confirm_wipe'))) {
        send({ cmd: 'wipe_memory' });
    }
});

async function loadMemoryStats() {
    try {
        const res = await fetch('/api/memory_stats');
        const d = await res.json();
        document.getElementById('memStats').textContent =
            t('memory_remembered', { conversations: d.conversations, facts: d.facts });
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
    showToast(t('testing_voice'), 'info', 2000);
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
loadLang();

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
