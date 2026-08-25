const canvas = document.getElementById('face');
const ctx = canvas.getContext('2d');

let state = 'idle';
let level = 0;
let smoothLevel = 0;
let t = 0;
let W = 360, H = 300, DPR = 1;

const gaze = { x: 0, y: 0, tx: 0, ty: 0 };
let mouse = { x: 0, y: 0 };
let nextSaccade = 0;
let nextBlink = 2;
let blink = 0;
let blinkPhase = 0;

function setState(s) {
    state = s;
    const el = document.getElementById('status');
    if (el) el.textContent =
        s === 'listening' ? 'Listening' :
        s === 'thinking' ? 'Processing' :
        s === 'speaking' ? 'Speaking' : 'Online';
}

function setLevel(v) { level = v; }

const COLORS = {
    idle:     { core: '#2f9fd8', glow: 'rgba(45,160,220,', ring: '#3fa9e0', eye: '#8fdcff' },
    listening:{ core: '#35e0ff', glow: 'rgba(50,220,255,', ring: '#48e6ff', eye: '#aef4ff' },
    thinking: { core: '#ffb347', glow: 'rgba(255,180,70,', ring: '#ffc061', eye: '#ffd9a0' },
    speaking: { core: '#54ffa8', glow: 'rgba(80,255,170,', ring: '#63ffbb', eye: '#c2ffe4' },
};

const bars = new Array(32).fill(0.15);

function resize() {
    const rect = canvas.getBoundingClientRect();
    DPR = window.devicePixelRatio || 1;
    W = Math.max(200, rect.width);
    H = Math.max(160, rect.height);
    canvas.width = W * DPR;
    canvas.height = H * DPR;
    ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
}

if (typeof ResizeObserver !== 'undefined') {
    new ResizeObserver(resize).observe(canvas);
} else {
    window.addEventListener('resize', resize);
}
resize();

document.addEventListener('mousemove', (e) => {
    const rect = canvas.getBoundingClientRect();
    mouse.x = Math.max(-1, Math.min(1, ((e.clientX - rect.left) / rect.width - 0.5) * 2));
    mouse.y = Math.max(-1, Math.min(1, ((e.clientY - rect.top) / rect.height - 0.5) * 2));
});

function drawEye(cx, cy, eyeW, eyeH, c, openness, pupilDx, pupilDy, pupilR) {
    ctx.save();
    ctx.translate(cx, cy);
    ctx.scale(1, Math.max(0.05, openness));

    const g = ctx.createRadialGradient(0, 0, 1, 0, 0, eyeW);
    g.addColorStop(0, '#f4feff');
    g.addColorStop(0.45, c.eye);
    g.addColorStop(1, c.glow + '0.10)');
    ctx.fillStyle = g;
    ctx.beginPath();
    ctx.ellipse(0, 0, eyeW, eyeH, 0, 0, Math.PI * 2);
    ctx.fill();

    ctx.strokeStyle = c.ring;
    ctx.lineWidth = 1.6;
    ctx.beginPath();
    ctx.ellipse(0, 0, eyeW, eyeH, 0, 0, Math.PI * 2);
    ctx.stroke();

    const px = pupilDx * (eyeW - pupilR - 3);
    const py = pupilDy * (eyeH - pupilR - 3);
    const pg = ctx.createRadialGradient(px, py, 0.5, px, py, pupilR);
    pg.addColorStop(0, '#06121f');
    pg.addColorStop(0.7, '#0b2237');
    pg.addColorStop(1, c.glow + '0.55)');
    ctx.fillStyle = pg;
    ctx.beginPath();
    ctx.arc(px, py, pupilR, 0, Math.PI * 2);
    ctx.fill();

    ctx.fillStyle = 'rgba(255,255,255,0.85)';
    ctx.beginPath();
    ctx.arc(px - pupilR * 0.3, py - pupilR * 0.35, pupilR * 0.18, 0, Math.PI * 2);
    ctx.fill();

    ctx.restore();
}

function draw() {
    t += 0.016;
    const c = COLORS[state] || COLORS.idle;
    ctx.clearRect(0, 0, W, H);

    smoothLevel += (level - smoothLevel) * 0.25;
    if (state !== 'listening') level *= 0.92;

    const CX = W / 2, CY = H / 2;

    // blink scheduling
    nextBlink -= 0.016;
    if (nextBlink <= 0 && blinkPhase === 0) {
        blinkPhase = 1;
        nextBlink = 2.2 + Math.random() * 3.8;
    }
    if (blinkPhase === 1) {
        blink += 0.14;
        if (blink >= 1) { blink = 1; blinkPhase = 2; }
    } else if (blinkPhase === 2) {
        blink -= 0.14;
        if (blink <= 0) { blink = 0; blinkPhase = 0; }
    }
    const openness = 1 - blink * (state === 'speaking' ? 0.85 : 1);

    // gaze behavior per state
    if (state === 'thinking') {
        gaze.tx = -0.45 + Math.sin(t * 0.9) * 0.15;
        gaze.ty = -0.5;
    } else if (state === 'listening') {
        gaze.tx = mouse.x * 0.5;
        gaze.ty = mouse.y * 0.4;
    } else if (state === 'speaking') {
        gaze.tx = mouse.x * 0.7;
        gaze.ty = mouse.y * 0.5;
    } else {
        if (t > nextSaccade) {
            gaze.tx = (Math.random() - 0.5) * 0.7;
            gaze.ty = (Math.random() - 0.5) * 0.4;
            nextSaccade = t + 1.5 + Math.random() * 3;
        }
    }
    gaze.x += (gaze.tx - gaze.x) * 0.08;
    gaze.y += (gaze.ty - gaze.y) * 0.08;

    const breathe = 1 + Math.sin(t * 1.6) * 0.03 + smoothLevel * 0.05;
    const R = Math.min(W, H) * 0.21 * breathe;

    // outer glow
    const g = ctx.createRadialGradient(CX, CY, R * 0.4, CX, CY, R * 2.6);
    g.addColorStop(0, c.glow + '0.22)');
    g.addColorStop(1, c.glow + '0)');
    ctx.fillStyle = g;
    ctx.beginPath(); ctx.arc(CX, CY, R * 2.6, 0, Math.PI * 2); ctx.fill();

    // rotating segmented rings
    const segCount = 24;
    const rotSpeed = state === 'thinking' ? 2.4 : state === 'listening' ? 1.1 : 0.45;
    for (let i = 0; i < segCount; i++) {
        const a0 = (i / segCount) * Math.PI * 2 + t * rotSpeed;
        const len = (Math.PI * 2 / segCount) * 0.42;
        const rr = R + Math.min(W, H) * 0.09 + Math.sin(t * 2 + i) * (state === 'listening' ? 6 : 2);
        ctx.strokeStyle = c.ring;
        ctx.globalAlpha = 0.35 + 0.3 * Math.sin(t * 3 + i * 2);
        ctx.lineWidth = 2.5;
        ctx.beginPath();
        ctx.arc(CX, CY, rr, a0, a0 + len);
        ctx.stroke();
    }
    ctx.globalAlpha = 1;

    // hexagonal frame
    ctx.strokeStyle = c.ring;
    ctx.lineWidth = 1.6;
    ctx.globalAlpha = 0.65;
    ctx.beginPath();
    for (let i = 0; i < 6; i++) {
        const a = (i / 6) * Math.PI * 2 + Math.PI / 6 + t * 0.12;
        const x = CX + Math.cos(a) * (R + Math.min(W, H) * 0.17);
        const y = CY + Math.sin(a) * (R + Math.min(W, H) * 0.17);
        i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
    }
    ctx.closePath();
    ctx.stroke();
    ctx.globalAlpha = 1;

    // eyes
    const eyeGap = R * 0.62;
    const eyeW = R * 0.42 * (state === 'listening' ? 1.08 : 1);
    const eyeH = R * 0.30 * breathe;
    const pupilR = R * 0.13 * (1 + smoothLevel * 0.9);
    const pupilDx = Math.max(-1, Math.min(1, gaze.x));
    const pupilDy = Math.max(-1, Math.min(1, gaze.y));
    drawEye(CX - eyeGap, CY - R * 0.08, eyeW, eyeH, c, openness, pupilDx, pupilDy, pupilR);
    drawEye(CX + eyeGap, CY - R * 0.08, eyeW, eyeH, c, openness, pupilDx, pupilDy, pupilR);

    // mouth waveform while speaking
    if (state === 'speaking') {
        for (let i = 0; i < bars.length; i++) {
            const target = 0.2 + Math.abs(Math.sin(t * 9 + i * 1.7)) * Math.random() * 0.8;
            bars[i] += (target - bars[i]) * 0.3;
        }
        const mouthY = CY + R * 0.55;
        const barW = R * 0.09;
        const gap = barW * 1.6;
        const totalW = bars.length * gap;
        for (let i = 0; i < bars.length; i++) {
            const x = CX - totalW / 2 + i * gap;
            const h = 3 + bars[i] * R * 0.35;
            ctx.strokeStyle = c.ring;
            ctx.globalAlpha = 0.9;
            ctx.lineWidth = barW * 0.7;
            ctx.lineCap = 'round';
            ctx.beginPath();
            ctx.moveTo(x, mouthY - h / 2);
            ctx.lineTo(x, mouthY + h / 2);
            ctx.stroke();
        }
        ctx.globalAlpha = 1;
    }

    requestAnimationFrame(draw);
}
requestAnimationFrame(draw);
