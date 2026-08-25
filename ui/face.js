const canvas = document.getElementById('face');
const ctx = canvas.getContext('2d');
const CW = canvas.width, CH = canvas.height;
const CX = CW / 2, CY = CH / 2;

let state = 'idle';
let level = 0;
let smoothLevel = 0;
let t = 0;

function setState(s) {
    state = s;
    document.getElementById('status').textContent =
        s === 'listening' ? 'Listening' :
        s === 'thinking' ? 'Processing' :
        s === 'speaking' ? 'Speaking' : 'Online';
}

function setLevel(v) { level = v; }

const COLORS = {
    idle:     { core: '#2f9fd8', glow: 'rgba(45,160,220,', ring: '#3fa9e0' },
    listening:{ core: '#35e0ff', glow: 'rgba(50,220,255,', ring: '#48e6ff' },
    thinking: { core: '#ffb347', glow: 'rgba(255,180,70,', ring: '#ffc061' },
    speaking: { core: '#54ffa8', glow: 'rgba(80,255,170,', ring: '#63ffbb' },
};

const bars = new Array(36).fill(0.15);

function draw() {
    t += 0.016;
    const c = COLORS[state] || COLORS.idle;
    ctx.clearRect(0, 0, CW, CH);

    smoothLevel += (level - smoothLevel) * 0.25;
    if (state !== 'listening') level *= 0.92;

    let breathe = 1 + Math.sin(t * 1.6) * 0.05;
    if (state === 'speaking') {
        for (let i = 0; i < bars.length; i++) {
            const target = 0.25 + Math.abs(Math.sin(t * 9 + i * 1.7)) * Math.random() * 0.75;
            bars[i] += (target - bars[i]) * 0.3;
        }
        breathe = 1 + Math.sin(t * 14) * 0.06;
    } else if (state === 'listening') {
        breathe = 1 + smoothLevel * 0.55;
    } else if (state === 'thinking') {
        breathe = 1 + Math.sin(t * 5) * 0.04;
    }

    const R = 62 * breathe;

    // outer glow
    const g = ctx.createRadialGradient(CX, CY, R * 0.4, CX, CY, R * 2.6);
    g.addColorStop(0, c.glow + '0.28)');
    g.addColorStop(1, c.glow + '0)');
    ctx.fillStyle = g;
    ctx.beginPath(); ctx.arc(CX, CY, R * 2.6, 0, Math.PI * 2); ctx.fill();

    // rotating segmented rings
    const segCount = 24;
    const rotSpeed = state === 'thinking' ? 2.4 : state === 'listening' ? 1.1 : 0.45;
    for (let i = 0; i < segCount; i++) {
        const a0 = (i / segCount) * Math.PI * 2 + t * rotSpeed;
        const len = (Math.PI * 2 / segCount) * 0.42;
        const rr = R + 26 + Math.sin(t * 2 + i) * (state === 'listening' ? 6 : 2);
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
        const x = CX + Math.cos(a) * (R + 52), y = CY + Math.sin(a) * (R + 52);
        i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
    }
    ctx.closePath();
    ctx.stroke();
    ctx.globalAlpha = 1;

    // inner core
    const coreG = ctx.createRadialGradient(CX - R*0.25, CY - R*0.25, 2, CX, CY, R);
    coreG.addColorStop(0, '#eafcff');
    coreG.addColorStop(0.35, c.core);
    coreG.addColorStop(1, c.glow + '0.12)');
    ctx.fillStyle = coreG;
    ctx.beginPath(); ctx.arc(CX, CY, R, 0, Math.PI * 2); ctx.fill();

    // triangle reactor mark
    ctx.strokeStyle = 'rgba(10,30,50,0.85)';
    ctx.lineWidth = 2.4;
    ctx.beginPath();
    for (let i = 0; i < 3; i++) {
        const a = (i / 3) * Math.PI * 2 - Math.PI / 2 + t * 0.3;
        const x = CX + Math.cos(a) * R * 0.45, y = CY + Math.sin(a) * R * 0.45;
        i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
    }
    ctx.closePath();
    ctx.stroke();

    // voice bars while speaking
    if (state === 'speaking') {
        const barR = R + 40;
        for (let i = 0; i < bars.length; i++) {
            const a = (i / bars.length) * Math.PI * 2;
            const h = 6 + bars[i] * 26;
            const x0 = CX + Math.cos(a) * barR, y0 = CY + Math.sin(a) * barR;
            const x1 = CX + Math.cos(a) * (barR + h), y1 = CY + Math.sin(a) * (barR + h);
            ctx.strokeStyle = c.ring;
            ctx.globalAlpha = 0.85;
            ctx.lineWidth = 3;
            ctx.lineCap = 'round';
            ctx.beginPath(); ctx.moveTo(x0, y0); ctx.lineTo(x1, y1); ctx.stroke();
        }
        ctx.globalAlpha = 1;
    }

    requestAnimationFrame(draw);
}
requestAnimationFrame(draw);
