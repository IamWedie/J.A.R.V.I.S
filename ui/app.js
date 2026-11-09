let ws;
let reconnectDelay = 1000;
let reconnectTimer = null;
const MAX_RECONNECT = 30000;

const JARVIS_TOKEN = document.querySelector('meta[name="jarvis-token"]')?.content || "";
let WSAUTHED = false;
let wsSessionId = null;
let authRejected = false;
const pendingSends = [];
function apiFetch(path, options = {}) {
    const headers = new Headers(options.headers || {});
    headers.set("X-JARVIS-Token", JARVIS_TOKEN);
    return fetch(path, { ...options, headers });
}

const chatlog = document.getElementById('chatlog');
const micBtn = document.getElementById('micBtn');
const textInput = document.getElementById('textInput');
const sendBtn = document.getElementById('sendBtn');
const approvalBox = document.getElementById('approvalBox');
const approvalText = document.getElementById('approvalText');
const approvalRequestId = document.getElementById('approvalRequestId');
const approvalRisk = document.getElementById('approvalRisk');
const approvalCaps = document.getElementById('approvalCaps');
const approvalScope = document.getElementById('approvalScope');
const btnAllowAlways = document.getElementById('btnAllowAlways');
const btnDenyEl = document.getElementById('btnDeny');
let currentApproval = null;
const modelSelect = document.getElementById('modelSelect');
const wakeBtn = document.getElementById('wakeBtn');
const settingsBtn = document.getElementById('settingsBtn');
const settingsPanel = document.getElementById('settingsPanel');
const voiceStatusEl = document.getElementById('voiceStatus');

const wizard = document.getElementById('setupWizard');
let validatedKey = null;

/* === APPROVAL (request-bound) === */
function clearApproval() {
    currentApproval = null;
    approvalBox.classList.add('hidden');
    delete approvalBox.dataset.requestId;
    approvalRequestId.textContent = '';
    approvalRisk.textContent = '';
    approvalCaps.textContent = '';
    approvalScope.textContent = '';
    approvalScope.classList.add('hidden');
    btnAllowAlways.classList.add('hidden');
}
function showApproval(msg) {
    if (!msg || typeof msg !== 'object' || typeof msg.request_id !== 'string' || !msg.request_id) {
        showToast(t('approval_invalid'), 'error', 8000);
        return;
    }
    currentApproval = msg;
    approvalBox.dataset.requestId = msg.request_id;
    approvalText.textContent = t('approval_requires', { description: msg.description || '' });
    approvalRequestId.textContent = t('approval_request_id', { id: msg.request_id });
    const allowedRisks = ['safe', 'guarded', 'sensitive', 'critical'];
    const risk = allowedRisks.includes(msg.risk) ? msg.risk : 'unknown';
    approvalRisk.className = 'approvalRisk risk-' + risk;
    approvalRisk.textContent = t('approval_risk', { risk: risk.toUpperCase() });
    const caps = Array.isArray(msg.capabilities) ? msg.capabilities.filter(c => typeof c === 'string') : [];
    const capsText = caps.length ? caps.join(', ') : '—';
    approvalCaps.textContent = t('approval_caps', { caps: capsText });
    if (msg.can_grant === true && typeof msg.grant_scope === 'string' && msg.grant_scope) {
        approvalScope.textContent = t('approval_scope', { scope: msg.grant_scope });
        approvalScope.classList.remove('hidden');
        btnAllowAlways.classList.remove('hidden');
    } else {
        approvalScope.textContent = '';
        approvalScope.classList.add('hidden');
        btnAllowAlways.classList.add('hidden');
    }
    approvalBox.classList.remove('hidden');
    btnDenyEl.focus();
    showToast(t('action_requires_approval'), 'warning', 8000);
}
function sendApproval(decision) {
    if (!currentApproval || typeof currentApproval.request_id !== 'string' || !currentApproval.request_id) {
        clearApproval();
        return;
    }
    const requestId = currentApproval.request_id;
    clearApproval();
    send({ cmd: 'approval_response', request_id: requestId, decision });
}

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
        approval_title: 'Approval required',
        approval_request_id: 'Request: {id}',
        approval_risk: 'Risk: {risk}',
        approval_caps: 'Capabilities: {caps}',
        approval_scope: 'Grant scope: {scope}',
        allow_always: 'ALLOW ALWAYS',
        approval_invalid: 'Ignored malformed approval request.',
        memory_remembered: '{conversations} messages remembered | {facts} facts',
        test: 'Testing voice...',
        confirm_wipe: 'Erase ALL memories and conversations permanently?',
        auth_failed: 'Authentication failed - access denied.',
        session_replaced: 'This window was replaced by another JARVIS window.',
        session_inactive: 'Session is no longer active.',
        security_status: 'Security',
        security_state: '{state} · {tools} tools',
        active_grants: 'Active grants',
        no_active_grants: 'No active grants',
        recent_audit: 'Recent security events',
        no_recent_events: 'No recent events',
        expires: 'expires',
        revoke: 'REVOKE',
        reset_audit: 'ARCHIVE & RESET AUDIT',
        confirm_revoke_grant: 'Revoke the "{tool}" grant? Every revocation is logged.',
        confirm_audit_reset_1: 'This ARCHIVES the audit chain, key, and policy files and starts a new audit chain. Old grants must be re-approved. Continue?',
        confirm_audit_reset_2: 'Archived material is never deleted, but a new key invalidates the old policy. RESET NOW?',
        grant_revoked: 'Grant revoked',
        audit_reset_done: 'Audit chain archived and reset',
        audit_reset_failed: 'Audit reset failed',
        security_unavailable: 'security unavailable',
        security_load_failed: 'Could not load security details',
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
        approval_title: 'مطلوب موافقة',
        approval_request_id: 'طلب: {id}',
        approval_risk: 'خطورة: {risk}',
        approval_caps: 'القدرات: {caps}',
        approval_scope: 'نطاق المنح: {scope}',
        allow_always: 'السماح دائماً',
        approval_invalid: 'تم تجاهل طلب موافقة غير صالح.',
        memory_remembered: '{conversations} رسائل | {facts} حقائق',
        test: 'جاري اختبار الصوت...',
        confirm_wipe: 'مسح جميع الذكريات والمحادثات نهائياً؟',
        auth_failed: 'فشلت المصادقة - تم رفض الوصول.',
        session_replaced: 'استُبدل هذا النافذة بنافذة جارفيس أخرى.',
        session_inactive: 'الجلسة لم تعد فعّالة.',
        security_status: 'الأمان',
        security_state: '{state} · {tools} أدوات',
        active_grants: 'المنح النشطة',
        no_active_grants: 'لا توجد منح نشطة',
        recent_audit: 'أحداث الأمان الأخيرة',
        no_recent_events: 'لا أحداث حديثة',
        expires: 'ينتهي',
        revoke: 'إلغاء',
        reset_audit: 'أرشفة وإعادة تعيين السجل',
        confirm_revoke_grant: 'إلغاء منح "{tool}"؟ سيتم تسجيل كل عملية إلغاء.',
        confirm_audit_reset_1: 'سيتم أرشفة سلسلة السجل والمفتاح وملفات السياسة وبدء سلسلة جديدة. يجب إعادة الموافقة على المنح القديمة. متابعة؟',
        confirm_audit_reset_2: 'المواد المؤرشفة لا تُحذف أبداً، لكن المفتاح الجديد يُبطل السياسة القديمة. إعادة التعيين الآن؟',
        grant_revoked: 'تم إلغاء المنح',
        audit_reset_done: 'تمت أرشفة سلسلة السجل وإعادة تعيينها',
        audit_reset_failed: 'فشلت إعادة تعيين السجل',
        security_unavailable: 'الأمان غير متوفر',
        security_load_failed: 'تعذر تحميل تفاصيل الأمان',
    },
};

let currentLang = 'en';
function t(key, vars, fallback) {
    const dict = I18N[currentLang] || I18N.en;
    let s = dict[key] !== undefined ? dict[key] : (I18N.en[key] !== undefined ? I18N.en[key] : (fallback !== undefined ? fallback : key));
    if (vars) { for (const k in vars) s = s.split('{' + k + '}').join(vars[k]); }
    return s;
}

function applyTranslations() {
    document.documentElement.lang = currentLang;
    document.documentElement.dir = currentLang === 'ar' ? 'rtl' : 'ltr';
    document.querySelectorAll('[data-i18n]').forEach(el => {
        const key = el.getAttribute('data-i18n');
        // Remember the original English text once so switching back to
        // English restores it exactly.
        if (el.dataset.i18nEn === undefined) el.dataset.i18nEn = el.textContent;
        const text = t(key, null, el.dataset.i18nEn);
        if (el.querySelector('[data-i18n]')) {
            el.childNodes.forEach(n => {
                if (n.nodeType === 3 && n.textContent.trim() !== '') n.textContent = text;
            });
        } else {
            el.textContent = text;
        }
    });
    document.querySelectorAll('[data-i18n-placeholder]').forEach(el => {
        if (el.dataset.i18nEnPlaceholder === undefined) {
            el.dataset.i18nEnPlaceholder = el.placeholder;
        }
        el.placeholder = t(el.getAttribute('data-i18n-placeholder'), null, el.dataset.i18nEnPlaceholder);
    });
    updateWakeBtn(wakeOn);
    updateVoiceStatus(voiceEnrolled);
}

async function loadLang() {
    try {
        const r = await apiFetch('/api/language');
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
        const r = await apiFetch('/api/setup_status');
        const d = await r.json();
        // Personal build: no license step. The server may still report the
        // legacy license flag for distribution builds; it is deliberately ignored.
        if (d.setup_needed) showWizard();
    } catch {}
}

function showWizard() {
    wizard.classList.remove('hidden');
    apiFetch('/api/terms').then(r => r.json()).then(d => {
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
    if (!key) { status.textContent = t('pasted_key_first'); return; }
    status.className = '';
    status.innerHTML = '<span class="spinner"></span>' + t('validating');
    btn.disabled = true;
    const r = await apiFetch('/api/setup_validate', {
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
        const r = await apiFetch('/api/system_check');
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
    await apiFetch('/api/setup_complete', {
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
        WSAUTHED = false;
        authRejected = false;
        pendingSends.length = 0;
        clearApproval();
        ws.send(JSON.stringify({ cmd: 'auth', token: JARVIS_TOKEN }));
    };
    ws.onmessage = onMessage;
    ws.onclose = (e) => {
        WSAUTHED = false;
        clearApproval();
        if (e && e.code === 4001) {
            document.getElementById('status').textContent = t('session_replaced');
            showToast(t('session_replaced'), 'warning', 10000);
            return;
        }
        if (authRejected) {
            document.getElementById('status').textContent = t('auth_failed');
            return;
        }
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
        case 'auth_ok':
            WSAUTHED = true;
            wsSessionId = msg.session_id;
            document.getElementById('status').textContent = t('online');
            while (pendingSends.length) {
                const queued = pendingSends.shift();
                if (ws && ws.readyState === 1) ws.send(JSON.stringify(queued));
            }
            break;
        case 'auth_error':
            WSAUTHED = false;
            clearApproval();
            if (!authRejected) showToast(t('auth_failed'), 'error', 8000);
            authRejected = true;
            break;
        case 'session_revoked':
            WSAUTHED = false;
            clearApproval();
            break;
        case 'session_inactive':
            WSAUTHED = false;
            clearApproval();
            showToast(t('session_inactive'), 'warning');
            break;
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
            showApproval(msg);
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
    }
}

function updateWakeBtn(on) {
    wakeOn = on;
    wakeBtn.textContent = on ? t('wake_on_label') : t('wake_off_label');
    wakeBtn.classList.toggle('on', on);
}

function send(obj) {
    if (!WSAUTHED) { pendingSends.push(obj); return; }
    if (ws && ws.readyState === 1) ws.send(JSON.stringify(obj));
}

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
document.getElementById('btnApprove').addEventListener('click', () => sendApproval('allow'));
document.getElementById('btnDeny').addEventListener('click', () => sendApproval('deny'));
btnAllowAlways.addEventListener('click', () => sendApproval('allow_always'));

/* === MODELS === */
async function loadModels() {
    try {
        const res = await apiFetch('/api/models');
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
    const hidden = settingsPanel.classList.toggle('hidden');
    settingsBtn.setAttribute('aria-expanded', !hidden);
    if (!hidden) loadSecurityPanel();
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
        const res = await apiFetch('/api/memory_stats');
        const d = await res.json();
        document.getElementById('memStats').textContent =
            t('memory_remembered', { conversations: d.conversations, facts: d.facts });
    } catch {}
}

/* === SECURITY PANEL === */
const securityStatusEl = document.getElementById('securityStatus');
const grantsListEl = document.getElementById('grantsList');
const recentAuditListEl = document.getElementById('recentAuditList');
const auditResetBtn = document.getElementById('auditResetBtn');
let securityLoadSeq = 0;

function clearSecurityPanel() {
    grantsListEl.textContent = '';
    recentAuditListEl.textContent = '';
    auditResetBtn.classList.add('hidden');
}

function fmtAuditTime(ts) {
    const value = Number(ts);
    return Number.isFinite(value)
        ? new Date(value * 1000).toISOString().replace('T', ' ').slice(0, 19)
        : '?';
}

function fmtGrantRow(grant) {
    const parts = [grant.tool, '—', grant.scope];
    if (Number.isFinite(Number(grant.expires_at))) {
        parts.push(`(${t('expires')} ${new Date(Number(grant.expires_at) * 1000)
            .toISOString().slice(0, 10)})`);
    }
    return parts.filter(p => p !== '' && p !== undefined).join(' ');
}

function renderGrantRow(grant) {
    if (typeof grant.grant_id !== 'string' || !grant.grant_id) return null;
    const item = document.createElement('div');
    item.className = 'securityItem';
    const label = document.createElement('span');
    label.textContent = fmtGrantRow(grant);
    item.appendChild(label);
    if (typeof grant.tool === 'string' && grant.tool) {
        const btn = document.createElement('button');
        btn.textContent = t('revoke');
        btn.setAttribute('aria-label', t('revoke') + ' ' + grant.tool);
        btn.addEventListener('click', () => revokeGrant(grant.grant_id, grant.tool));
        item.appendChild(btn);
    }
    return item;
}

async function revokeGrant(grantId, tool) {
    if (typeof grantId !== 'string' || !grantId) return;
    if (!confirm(t('confirm_revoke_grant', { tool: tool || grantId }))) return;
    try {
        const res = await apiFetch(
            '/api/security/grants/' + encodeURIComponent(grantId) + '/revoke',
            { method: 'POST' }
        );
        if (!res.ok) throw new Error('revoke_failed');
        showToast(t('grant_revoked'), 'success');
    } catch {
        showToast(t('security_load_failed'), 'error');
    }
    loadSecurityPanel();
}

async function auditReset() {
    if (!confirm(t('confirm_audit_reset_1'))) return;
    if (!confirm(t('confirm_audit_reset_2'))) return;
    auditResetBtn.disabled = true;
    try {
        const res = await apiFetch('/api/security/audit_reset', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ confirm: true }),
        });
        if (!res.ok) throw new Error('reset_failed');
        showToast(t('audit_reset_done'), 'success', 8000);
    } catch {
        showToast(t('audit_reset_failed'), 'error', 8000);
    } finally {
        auditResetBtn.disabled = false;
    }
    loadSecurityPanel();
}

async function loadSecurityPanel() {
    const seq = ++securityLoadSeq;
    clearSecurityPanel();
    securityStatusEl.textContent = t('security_status');
    try {
        const [statusRes, grantsRes] = await Promise.all([
            apiFetch('/api/security/status'),
            apiFetch('/api/security/grants'),
        ]);
        const statusData = await statusRes.json();
        const grantsData = await grantsRes.json();
        if (seq !== securityLoadSeq) return; // stale; a newer load superseded it
        if (!statusRes.ok || !grantsRes.ok) throw new Error('security_api_unavailable');

        const state = statusData.status || 'unknown';
        const tools = Array.isArray(statusData.tools) ? statusData.tools.length : 0;
        securityStatusEl.textContent = t('security_state', { state, tools });
        auditResetBtn.classList.toggle('hidden', state !== 'audit_locked');

        recentAuditListEl.textContent = '';
        const events = Array.isArray(statusData.recent_audit) ? statusData.recent_audit : [];
        const latest = events.slice(-10).reverse(); // newest first
        if (!latest.length) {
            const empty = document.createElement('div');
            empty.className = 'securityItem';
            empty.textContent = t('no_recent_events');
            recentAuditListEl.appendChild(empty);
        } else {
            for (const e of latest) {
                const row = document.createElement('div');
                row.className = 'securityItem';
                const parts = [
                    fmtAuditTime(e.ts),
                    e.event_type || '?',
                    e.tool || '',
                    e.decision || '',
                ].filter(p => p !== '');
                row.textContent = parts.join(' · ');
                recentAuditListEl.appendChild(row);
            }
        }

        grantsListEl.textContent = '';
        const grants = Array.isArray(grantsData.grants) ? grantsData.grants : [];
        if (!grants.length) {
            const empty = document.createElement('div');
            empty.className = 'securityItem';
            empty.textContent = t('no_active_grants');
            grantsListEl.appendChild(empty);
        } else {
            for (const grant of grants) {
                const row = renderGrantRow(grant);
                if (row) grantsListEl.appendChild(row);
            }
        }
    } catch {
        clearSecurityPanel();
        securityStatusEl.textContent = t('security_unavailable');
    }
}

auditResetBtn.addEventListener('click', auditReset);

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

/* === VERSION === */
async function loadVersion() {
    try {
        const r = await apiFetch('/api/status');
        const d = await r.json();
        document.getElementById('versionLabel').textContent = 'v' + (d.version || '?');
    } catch {}
}

/* === LANGUAGE === */
const langSelect = document.getElementById('langSelect');
langSelect.addEventListener('change', () => {
    currentLang = langSelect.value;
    applyTranslations();
    send({ cmd: 'set_language', lang: currentLang });
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
        const r = await apiFetch('/api/status');
        const d = await r.json();
        offlineBanner.classList.toggle('hidden', d.online);
    } catch {
        offlineBanner.classList.remove('hidden');
    }
}
setInterval(checkHealth, 30000);
checkHealth();
