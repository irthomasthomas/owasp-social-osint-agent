'use strict';

const COLORS = ['#00e59b', '#47b3ff', '#ffb347', '#ff4d6a', '#a78bfa', '#f472b6', '#34d399', '#fbbf24'];
const API = '/api/v1';
const PANEL_ICONS = { report:'fa-file-lines', timeline:'fa-chart-bar', history:'fa-clock-rotate-left', contacts:'fa-address-book', entities:'fa-tags' };
const PANEL_TITLES = { report:'Report', timeline:'Timeline', history:'History', contacts:'Contacts', entities:'Entities' };
const LS_LAYOUT_KEY = 'osint-agent-layout';

const state = {
    sessions: [], currentSession: null, activeQueryId: null, runningJobId: null,
    sseSource: null, cacheEntries: [], contacts: [], contactsDismissed: [],
    contactsFilter: '', mediaItems: [], cacheFilter: 'all', activePage: 'dashboard',
    mtTargets: [], theme: 'dark', timelineEvents: [], panelFilter: '',
};

let panelZCounter = 10;
let panelsInited = false;
const SNAP_THRESHOLD = 15;
const SNAP_PAD = 4;
const ALL_PANELS = ['report','timeline','history','contacts','entities'];

// ═══════════════ THEME ═══════════════
function applyTheme(t) {
    state.theme = t;
    document.documentElement.setAttribute('data-theme', t);
    localStorage.setItem('osint-agent-theme', t);
    const moon = document.getElementById('themeIconMoon');
    const sun = document.getElementById('themeIconSun');
    if (moon && sun) { moon.style.display = t === 'dark' ? '' : 'none'; sun.style.display = t === 'light' ? '' : 'none'; }
    if (panelsInited) requestAnimationFrame(() => drawNetworkGraph());
}
function toggleTheme() { applyTheme(state.theme === 'dark' ? 'light' : 'dark'); }
function getCssVar(n) { return getComputedStyle(document.documentElement).getPropertyValue(n).trim(); }

// ═══════════════ API ═══════════════
async function api(method, path, body) {
    const res = await fetch(API + path, { method, headers: { 'Content-Type': 'application/json' }, body: body !== undefined ? JSON.stringify(body) : undefined });
    if (!res.ok) { const err = await res.json().catch(() => ({ detail: res.statusText })); throw new Error(err.detail || `HTTP ${res.status}`); }
    if (res.status === 204) return null;
    return res.json();
}
const apiGet = p => api('GET', p);
const apiPost = (p, b) => api('POST', p, b);
const apiPut = (p, b) => api('PUT', p, b);
const apiDel = p => api('DELETE', p);

// ═══════════════ UTILS ═══════════════
function esc(s) { return String(s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#x27;'); }
function fmtAge(iso) { if (!iso) return ''; const s = (Date.now() - new Date(iso)) / 1000; if (s < 60) return `${Math.round(s)}s ago`; if (s < 3600) return `${Math.round(s/60)}m ago`; if (s < 86400) return `${Math.round(s/3600)}h ago`; return `${Math.round(s/86400)}d ago`; }
function fmtDate(iso) { if (!iso) return ''; return new Date(iso).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }); }
function hashStr(s) { let h = 0; for (let i = 0; i < s.length; i++) h = ((h << 5) - h) + s.charCodeAt(i); return Math.abs(h); }

function toast(msg, type = 'success') {
    const c = document.getElementById('toastContainer');
    const t = document.createElement('div');
    t.className = 'toast ' + type;
    const icons = { success: 'fa-check-circle', error: 'fa-exclamation-circle', info: 'fa-info-circle' };
    t.innerHTML = `<i class="fa-solid ${icons[type] || icons.info}"></i> ${esc(msg)}`;
    c.appendChild(t);
    setTimeout(() => { t.style.opacity = '0'; t.style.transform = 'translateX(20px)'; t.style.transition = 'all 0.3s'; setTimeout(() => t.remove(), 300); }, 3500);
}

function openModal(id) { document.getElementById(id).classList.add('show'); }
function closeModal(id) { document.getElementById(id).classList.remove('show'); }

function setStatus(running, text) {
    const ind = document.getElementById('statusIndicator');
    ind.style.display = running ? 'flex' : 'none';
    if (text) document.getElementById('statusText').textContent = text;
}

function toggleSection(btn) { btn.closest('.report-section').classList.toggle('collapsed'); }

// ═══════════════ PANEL SYSTEM ═══════════════

function getDefaultGroups() {
    const cw = Math.max(600, document.getElementById('panelCanvas')?.clientWidth || 900);
    const ch = Math.max(400, document.getElementById('panelCanvas')?.clientHeight || 600);
    const rw = Math.round(cw * 0.56), rw2 = cw - rw - 12;
    const th = Math.round(ch * 0.42), bh = ch - th - 12;
    const hw = Math.round(rw2 * 0.48), cw2 = rw2 - hw - 12;
    const conh = Math.round(bh * 0.65), enh = bh - conh - 12;
    return [
        { id:'g0', panels:['report'], active:'report', x:0, y:0, w:rw, h:ch },
        { id:'g1', panels:['timeline'], active:'timeline', x:rw+12, y:0, w:rw2, h:th },
        { id:'g2', panels:['history'], active:'history', x:rw+12, y:th+12, w:hw, h:bh },
        { id:'g3', panels:['contacts'], active:'contacts', x:rw+12+hw+12, y:th+12, w:cw2, h:conh },
        { id:'g4', panels:['entities'], active:'entities', x:rw+12+hw+12, y:th+12+conh+12, w:cw2, h:enh },
    ];
}

function loadSavedGroups() {
    try {
        const raw = localStorage.getItem(LS_LAYOUT_KEY);
        if (!raw) return null;
        const data = JSON.parse(raw);
        if (data && data.version === 2 && Array.isArray(data.groups)) return data.groups;
        let groups = [], i = 0;
        for (const [pid, pos] of Object.entries(data || {})) {
            if (!pos || typeof pos.x !== 'number') continue;
            groups.push({ id:'g'+(i++), panels:[pid], active:pid, x:pos.x, y:pos.y, w:pos.w, h:pos.collapsed?(pos.h||300):pos.h, collapsed:!!pos.collapsed, pinned:!!pos.pinned });
        }
        return groups.length ? groups : null;
    } catch { return null; }
}

function savePanelLayout() {
    const groups = [];
    document.querySelectorAll('.panel-group').forEach(el => {
        const panels = []; let active = null;
        el.querySelectorAll('.pg-tab').forEach(t => { panels.push(t.dataset.panel); if (t.classList.contains('active')) active = t.dataset.panel; });
        groups.push({
            id: el.dataset.groupId, panels, active: active || panels[0],
            x: el.offsetLeft, y: el.offsetTop, w: el.offsetWidth,
            h: el.classList.contains('collapsed') ? (parseFloat(el.dataset.savedH)||300) : el.offsetHeight,
            collapsed: el.classList.contains('collapsed'), pinned: el.dataset.pinned === 'true',
        });
    });
    localStorage.setItem(LS_LAYOUT_KEY, JSON.stringify({ version: 2, groups }));
}

function buildGroupEl(g) {
    const el = document.createElement('div');
    el.className = 'panel-group' + (g.collapsed ? ' collapsed' : '');
    el.dataset.groupId = g.id;
    el.dataset.savedH = g.h || 300;
    if (g.pinned) el.dataset.pinned = 'true';
    el.style.cssText = `left:${g.x}px;top:${g.y}px;width:${g.w}px;height:${g.collapsed?32:g.h}px;z-index:${++panelZCounter}`;

    ['n','ne','e','se','s','sw','w','nw'].forEach(d => {
        const rh = document.createElement('div'); rh.className = 'pg-rh pg-rh-'+d; rh.dataset.dir = d; el.appendChild(rh);
    });

    const dz = document.createElement('div'); dz.className = 'pg-drop-zone'; el.appendChild(dz);

    const hdr = document.createElement('div'); hdr.className = 'pg-header';
    const tabBar = document.createElement('div'); tabBar.className = 'pg-tabs';
    g.panels.forEach(pid => {
        const t = document.createElement('div');
        t.className = 'pg-tab' + (pid === g.active ? ' active' : '');
        t.dataset.panel = pid;
        t.innerHTML = `<i class="fa-solid ${PANEL_ICONS[pid]||'fa-cube'}"></i><span class="pg-tab-label">${PANEL_TITLES[pid]||pid}</span>`;
        t.addEventListener('mousedown', e => startTabDrag(e, g.id, pid));
        tabBar.appendChild(t);
    });
    hdr.appendChild(tabBar);

    const acts = document.createElement('div'); acts.className = 'pg-actions';
    acts.innerHTML = `<button class="fp-btn pg-pin-btn" onclick="toggleGroupPin('${g.id}')" title="Pin"><i class="fa-solid fa-thumbtack"></i></button><button class="fp-btn" onclick="toggleGroupCollapse('${g.id}')" title="Collapse"><i class="fa-solid fa-minus"></i></button>`;
    hdr.appendChild(acts);
    el.appendChild(hdr);

    const content = document.createElement('div'); content.className = 'pg-content';
    g.panels.forEach(pid => {
        const pane = document.createElement('div'); pane.className = 'pg-pane'; pane.dataset.panel = pid;
        pane.style.display = pid === g.active ? '' : 'none';
        content.appendChild(pane);
    });
    el.appendChild(content);

    document.getElementById('panelCanvas').appendChild(el);
    initGroupDrag(el);
    initGroupResize(el);
    el.addEventListener('mousedown', () => bringToFront(el));
    return el;
}

function switchTab(groupId, panelId) {
    const el = document.querySelector(`.panel-group[data-group-id="${groupId}"]`);
    if (!el) return;
    el.querySelectorAll('.pg-tab').forEach(t => t.classList.toggle('active', t.dataset.panel === panelId));
    el.querySelectorAll('.pg-pane').forEach(p => p.style.display = p.dataset.panel === panelId ? '' : 'none');
    savePanelLayout();
    if (panelId === 'contacts') requestAnimationFrame(() => drawNetworkGraph());
    if (panelId === 'timeline') requestAnimationFrame(() => { if (state.timelineEvents?.length) updatePanelTimeline(); });
}

function bringToFront(el) {
    document.querySelectorAll('.panel-group').forEach(p => p.classList.remove('active-panel'));
    document.querySelectorAll('.panel-group:not([data-pinned="true"])').forEach(p => {
        p.style.zIndex = Math.max(10, parseInt(p.style.zIndex || '10'));
    });
    const pinnedMax = Math.max(10, ...[...document.querySelectorAll('.panel-group[data-pinned="true"]')].map(p => parseInt(p.style.zIndex || '10')));
    if (el.dataset.pinned === 'true') {
        el.style.zIndex = Math.max(pinnedMax + 1, ++panelZCounter);
    } else {
        const unpinnedMax = Math.max(0, ...[...document.querySelectorAll('.panel-group:not([data-pinned="true"])')].map(p => parseInt(p.style.zIndex || '10')));
        el.style.zIndex = Math.max(unpinnedMax + 1, ++panelZCounter);
    }
    el.classList.add('active-panel');
}

function toggleGroupPin(groupId) {
    const el = document.querySelector(`.panel-group[data-group-id="${groupId}"]`);
    if (!el) return;
    el.dataset.pinned = el.dataset.pinned === 'true' ? 'false' : 'true';
    restackZIndices();
    savePanelLayout();
}

function restackZIndices() {
    const pinned = [...document.querySelectorAll('.panel-group[data-pinned="true"]')];
    const unpinned = [...document.querySelectorAll('.panel-group:not([data-pinned="true"])')];
    let z = 10;
    unpinned.forEach(g => g.style.zIndex = ++z);
    pinned.forEach(g => g.style.zIndex = ++z);
    panelZCounter = z;
}

function toggleGroupCollapse(groupId) {
    const el = document.querySelector(`.panel-group[data-group-id="${groupId}"]`);
    if (!el) return;
    el.classList.toggle('collapsed');
    if (!el.classList.contains('collapsed')) {
        el.style.height = (parseFloat(el.dataset.savedH)||300) + 'px';
    }
    savePanelLayout();
    const activeTab = el.querySelector('.pg-tab.active');
    if (activeTab?.dataset.panel === 'contacts') requestAnimationFrame(() => drawNetworkGraph());
}

function resetLayout() {
    localStorage.removeItem(LS_LAYOUT_KEY);
    document.querySelectorAll('.panel-group').forEach(p => p.remove());
    panelsInited = false;
    initPanels();
    if (state.currentSession && state.activeQueryId) {
        const entry = state.currentSession.query_history?.find(e => e.query_id === state.activeQueryId);
        if (entry) updatePanelContents(entry);
    }
    toast('Layout reset', 'info');
}

// --- Snap ---
function getOrCreateSnapGuide() {
    let g = document.getElementById('snapGuide');
    if (!g) { g = document.createElement('div'); g.id = 'snapGuide'; g.className = 'snap-guide'; g.style.display = 'none'; document.getElementById('panelCanvas')?.appendChild(g); }
    return g;
}
function hideSnapGuide() { const g = document.getElementById('snapGuide'); if (g) g.style.display = 'none'; }

function calcSnap(el, newX, newY) {
    const canvas = document.getElementById('panelCanvas');
    const cw = canvas.clientWidth, ch = canvas.clientHeight;
    const pw = el.offsetWidth, ph = el.offsetHeight;
    let snapX = null, snapY = null;

    if (Math.abs(newX) < SNAP_THRESHOLD) snapX = SNAP_PAD;
    else if (Math.abs(newX + pw - cw) < SNAP_THRESHOLD) snapX = cw - pw - SNAP_PAD;
    if (Math.abs(newY) < SNAP_THRESHOLD) snapY = SNAP_PAD;
    else if (Math.abs(newY + ph - ch) < SNAP_THRESHOLD) snapY = ch - ph - SNAP_PAD;

    document.querySelectorAll('.panel-group').forEach(other => {
        if (other === el) return;
        const ox = other.offsetLeft, oy = other.offsetTop, ow = other.offsetWidth, oh = other.offsetHeight;
        const oR = ox+ow, oB = oy+oh, nR = newX+pw, nB = newY+ph;
        if (snapX === null) {
            if (Math.abs(newX-ox)<SNAP_THRESHOLD) snapX=ox;
            else if (Math.abs(newX-oR)<SNAP_THRESHOLD) snapX=oR;
            else if (Math.abs(nR-ox)<SNAP_THRESHOLD) snapX=ox-pw;
            else if (Math.abs(nR-oR)<SNAP_THRESHOLD) snapX=oR-pw;
        }
        if (snapY === null) {
            if (Math.abs(newY-oy)<SNAP_THRESHOLD) snapY=oy;
            else if (Math.abs(newY-oB)<SNAP_THRESHOLD) snapY=oB;
            else if (Math.abs(nB-oy)<SNAP_THRESHOLD) snapY=oy-ph;
            else if (Math.abs(nB-oB)<SNAP_THRESHOLD) snapY=oB-ph;
        }
    });

    if (snapX !== null) newX = snapX;
    if (snapY !== null) newY = snapY;
    const g = getOrCreateSnapGuide();
    if (snapX !== null || snapY !== null) {
        g.style.display = 'block';
        if (snapX !== null && snapY !== null) { g.style.left=(snapX-1)+'px'; g.style.top=(snapY-1)+'px'; g.style.width='2px'; g.style.height='2px'; }
        else if (snapX !== null) { g.style.left=(snapX-1)+'px'; g.style.top='0'; g.style.width='2px'; g.style.height=ch+'px'; }
        else { g.style.left='0'; g.style.top=(snapY-1)+'px'; g.style.width=cw+'px'; g.style.height='2px'; }
    } else { g.style.display = 'none'; }
    return { x: newX, y: newY };
}

// --- Merge ---
function findMergeTarget(dragEl, x, y, w, h) {
    let best = null, bestArea = 0;
    document.querySelectorAll('.panel-group').forEach(other => {
        if (other === dragEl) return;
        const ox = other.offsetLeft, oy = other.offsetTop, ow = other.offsetWidth, oh = other.offsetHeight;
        const olX = Math.max(0, Math.min(x+w, ox+ow) - Math.max(x, ox));
        const olY = Math.max(0, Math.min(y+h, oy+oh) - Math.max(y, oy));
        const area = olX * olY;
        if (area > bestArea) { bestArea = area; best = other; }
    });
    const dragA = w * h;
    const thresh = Math.min(dragA, best ? best.offsetWidth * best.offsetHeight : 0) * 0.35;
    return bestArea > thresh ? best : null;
}
function showMergeTarget(el) { el.querySelector('.pg-drop-zone')?.classList.add('show'); }
function hideMergeTargets() { document.querySelectorAll('.pg-drop-zone.show').forEach(d => d.classList.remove('show')); }

function doMerge(srcEl, tgtEl) {
    const srcPanes = srcEl.querySelectorAll('.pg-pane');
    const tgtContent = tgtEl.querySelector('.pg-content');
    const tgtTabBar = tgtEl.querySelector('.pg-tabs');
    const tgtId = tgtEl.dataset.groupId;
    srcPanes.forEach(pane => {
        pane.style.display = 'none';
        tgtContent.appendChild(pane);
        const pid = pane.dataset.panel;
        const tab = document.createElement('div');
        tab.className = 'pg-tab';
        tab.dataset.panel = pid;
        tab.innerHTML = `<i class="fa-solid ${PANEL_ICONS[pid]||'fa-cube'}"></i><span class="pg-tab-label">${PANEL_TITLES[pid]||pid}</span>`;
        tab.addEventListener('mousedown', e => startTabDrag(e, tgtId, pid));
        tgtTabBar.appendChild(tab);
        populatePaneBody(tgtEl, pid);
    });
    srcEl.remove();
    const firstPid = srcPanes[0]?.dataset.panel;
    if (firstPid) switchTab(tgtId, firstPid);
    savePanelLayout();
}

// --- Group Drag ---
function initGroupDrag(el) {
    const hdr = el.querySelector('.pg-header');
    let dragging = false, sx, sy, sL, sT;
    function begin(cx, cy) {
        if (el.dataset.pinned === 'true') return false;
        dragging = true; sx = cx; sy = cy; sL = el.offsetLeft; sT = el.offsetTop; bringToFront(el); return true;
    }
    function move(cx, cy) {
        if (!dragging) return;
        const canvas = document.getElementById('panelCanvas');
        const cw = canvas.clientWidth, ch = canvas.clientHeight, pw = el.offsetWidth, ph = el.offsetHeight;
        let nx = Math.max(-pw+60, Math.min(cw-60, sL+cx-sx));
        let ny = Math.max(0, Math.min(ch-20, sT+cy-sy));
        const s = calcSnap(el, nx, ny);
        el.style.left = s.x + 'px'; el.style.top = s.y + 'px';
        hideMergeTargets();
        const mt = findMergeTarget(el, s.x, s.y, pw, ph);
        if (mt) showMergeTarget(mt);
    }
    function end() {
        if (!dragging) return; dragging = false; hideSnapGuide();
        const mt = document.querySelector('.pg-drop-zone.show')?.closest('.panel-group');
        hideMergeTargets();
        if (mt && mt !== el) { doMerge(el, mt); }
        else { savePanelLayout(); }
    }
    hdr.addEventListener('mousedown', e => {
        if (e.target.closest('.fp-btn') || e.target.closest('.pg-tab')) return;
        e.preventDefault(); begin(e.clientX, e.clientY);
        const onM = ev => move(ev.clientX, ev.clientY);
        const onU = () => { document.removeEventListener('mousemove', onM); document.removeEventListener('mouseup', onU); end(); };
        document.addEventListener('mousemove', onM); document.addEventListener('mouseup', onU);
    });
    hdr.addEventListener('touchstart', e => {
        if (e.target.closest('.fp-btn') || e.target.closest('.pg-tab')) return;
        if (!begin(e.touches[0].clientX, e.touches[0].clientY)) return;
        const onM = ev => { ev.preventDefault(); move(ev.touches[0].clientX, ev.touches[0].clientY); };
        const onU = () => { document.removeEventListener('touchmove', onM); document.removeEventListener('touchend', onU); end(); };
        document.addEventListener('touchmove', onM, { passive: false }); document.addEventListener('touchend', onU);
    }, { passive: true });
}

// --- 8-direction Resize with linked snap resize ---
const LINK_THRESH = 4;

function findLinkedNeighbors(el, edge) {
    const L = el.offsetLeft, T = el.offsetTop, R = L + el.offsetWidth, B = T + el.offsetHeight;
    const neighbors = [];
    document.querySelectorAll('.panel-group').forEach(g => {
        if (g === el || g.dataset.pinned === 'true') return;
        const gL = g.offsetLeft, gT = g.offsetTop, gR = gL + g.offsetWidth, gB = gT + g.offsetHeight;
        const hOverlap = Math.max(0, Math.min(R, gR) - Math.max(L, gL));
        const vOverlap = Math.max(0, Math.min(B, gB) - Math.max(T, gT));
        if (edge === 'e' && Math.abs(R - gL) < LINK_THRESH && hOverlap > 20) neighbors.push({ el: g, edge: 'w', axis: 'x' });
        else if (edge === 'w' && Math.abs(L - gR) < LINK_THRESH && hOverlap > 20) neighbors.push({ el: g, edge: 'e', axis: 'x' });
        else if (edge === 's' && Math.abs(B - gT) < LINK_THRESH && vOverlap > 20) neighbors.push({ el: g, edge: 'n', axis: 'y' });
        else if (edge === 'n' && Math.abs(T - gB) < LINK_THRESH && vOverlap > 20) neighbors.push({ el: g, edge: 's', axis: 'y' });
    });
    return neighbors;
}

function applyLinkedResize(neighbor, edge, dx, dy) {
    const g = neighbor.el;
    let L = g.offsetLeft, T = g.offsetTop, W = g.offsetWidth, H = g.offsetHeight;
    if (edge === 'w') { const nw = Math.max(220, W - dx); L = L + W - nw; W = nw; }
    else if (edge === 'e') { W = Math.max(220, W + dx); }
    else if (edge === 'n') { const nh = Math.max(80, H - dy); T = T + H - nh; H = nh; }
    else if (edge === 's') { H = Math.max(80, H + dy); }
    g.style.left = L+'px'; g.style.top = T+'px'; g.style.width = W+'px'; g.style.height = H+'px';
}

function initGroupResize(el) {
    const MIN_W = 220, MIN_H = 80;
    function begin(cx, cy) {
        if (el.dataset.pinned === 'true') return null;
        bringToFront(el);
        return { sx: cx, sy: cy, sL: el.offsetLeft, sT: el.offsetTop, sW: el.offsetWidth, sH: el.offsetHeight };
    }
    function apply(st, dir, cx, cy) {
        if (!st) return;
        const canvas = document.getElementById('panelCanvas');
        const maxW = canvas.clientWidth, maxH = canvas.clientHeight;
        const dx = cx - st.sx, dy = cy - st.sy;
        let L = st.sL, T = st.sT, W = st.sW, H = st.sH;
        if (dir.includes('e')) W = Math.max(MIN_W, Math.min(maxW - L, st.sW + dx));
        if (dir.includes('w')) { const nw = Math.max(MIN_W, Math.min(st.sW - dx)); L = st.sL + st.sW - nw; W = nw; }
        if (dir.includes('s')) H = Math.max(MIN_H, Math.min(maxH - T, st.sH + dy));
        if (dir.includes('n') && !el.classList.contains('collapsed')) { const nh = Math.max(MIN_H, Math.min(st.sH - dy)); T = st.sT + st.sH - nh; H = nh; }
        el.style.left = L+'px'; el.style.top = T+'px'; el.style.width = W+'px'; el.style.height = H+'px';

        const realDx = (L - st.sL) !== 0 ? (L + W) - (st.sL + st.sW) : W - st.sW;
        const realDy = (T - st.sT) !== 0 ? (T + H) - (st.sT + st.sH) : H - st.sH;
        if (dir.includes('e') && realDx !== 0) findLinkedNeighbors(el, 'e').forEach(n => applyLinkedResize(n, n.edge, realDx, 0));
        if (dir.includes('w') && (L - st.sL) !== 0) findLinkedNeighbors(el, 'w').forEach(n => applyLinkedResize(n, n.edge, L - st.sL, 0));
        if (dir.includes('s') && realDy !== 0) findLinkedNeighbors(el, 's').forEach(n => applyLinkedResize(n, n.edge, 0, realDy));
        if (dir.includes('n') && (T - st.sT) !== 0) findLinkedNeighbors(el, 'n').forEach(n => applyLinkedResize(n, n.edge, 0, T - st.sT));
    }
    el.querySelectorAll('.pg-rh').forEach(rh => {
        rh.addEventListener('mousedown', e => {
            e.preventDefault(); e.stopPropagation();
            const st = begin(e.clientX, e.clientY); if (!st) return;
            const dir = rh.dataset.dir;
            const onM = ev => apply(st, dir, ev.clientX, ev.clientY);
            const onU = () => { document.removeEventListener('mousemove', onM); document.removeEventListener('mouseup', onU); savePanelLayout(); };
            document.addEventListener('mousemove', onM); document.addEventListener('mouseup', onU);
        });
        rh.addEventListener('touchstart', e => {
            e.preventDefault(); e.stopPropagation();
            const st = begin(e.touches[0].clientX, e.touches[0].clientY); if (!st) return;
            const dir = rh.dataset.dir;
            const onM = ev => { ev.preventDefault(); apply(st, dir, ev.touches[0].clientX, ev.touches[0].clientY); };
            const onU = () => { document.removeEventListener('touchmove', onM); document.removeEventListener('touchend', onU); savePanelLayout(); };
            document.addEventListener('touchmove', onM, { passive: false }); document.addEventListener('touchend', onU);
        }, { passive: false });
    });
}

// --- Tab Drag (detach from multi-tab group) ---
function startTabDrag(e, groupId, panelId) {
    if (e.button !== 0) return;
    const el = document.querySelector(`.panel-group[data-group-id="${groupId}"]`);
    if (!el || el.querySelectorAll('.pg-tab').length <= 1) return;
    const isPinned = el.dataset.pinned === 'true';
    if (isPinned) { switchTab(groupId, panelId); return; }
    e.preventDefault();
    const sx = e.clientX, sy = e.clientY;
    let detached = false, lx = sx, ly = sy, ghost = null;

    function onM(ev) {
        lx = ev.clientX; ly = ev.clientY;
        if (!detached && (Math.abs(lx-sx)>8 || Math.abs(ly-sy)>8)) {
            detached = true;
            ghost = document.createElement('div');
            ghost.className = 'panel-group detach-ghost';
            ghost.innerHTML = `<div class="pg-header"><div class="pg-tabs"><div class="pg-tab active"><i class="fa-solid ${PANEL_ICONS[panelId]||'fa-cube'}"></i><span class="pg-tab-label">${PANEL_TITLES[panelId]||panelId}</span></div></div></div>`;
            document.body.appendChild(ghost);
            el.querySelectorAll('.pg-pane').forEach(p => { if (p.dataset.panel===panelId) p.style.display='none'; });
            const first = el.querySelector(`.pg-tab:not([data-panel="${panelId}"])`);
            if (first) switchTab(groupId, first.dataset.panel);
        }
        if (detached && ghost) {
            ghost.style.left = (lx-150)+'px'; ghost.style.top = (ly-20)+'px';
            hideMergeTargets();
            const canvas = document.getElementById('panelCanvas');
            const r = canvas.getBoundingClientRect();
            document.querySelectorAll('.panel-group').forEach(g => {
                if (g === el) return;
                const gr = g.getBoundingClientRect();
                const olX = Math.max(0, Math.min(lx, gr.right) - Math.max(lx-300, gr.left));
                const olY = Math.max(0, Math.min(ly, gr.bottom) - Math.max(ly-200, gr.top));
                if (olX*olY > 3000) showMergeTarget(g);
            });
        }
    }
    function onU() {
        document.removeEventListener('mousemove', onM); document.removeEventListener('mouseup', onU);
        if (ghost) ghost.remove();
        hideMergeTargets();
        if (!detached) { switchTab(groupId, panelId); return; }
        const canvas = document.getElementById('panelCanvas');
        const r = canvas.getBoundingClientRect();
        const cx = lx-r.left, cy = ly-r.top;
        const mergeTarget = document.querySelector('.pg-drop-zone.show')?.closest('.panel-group');
        if (mergeTarget && mergeTarget !== el) {
            const pane = el.querySelector(`.pg-pane[data-panel="${panelId}"]`);
            const tab = el.querySelector(`.pg-tab[data-panel="${panelId}"]`);
            pane.style.display = 'none';
            mergeTarget.querySelector('.pg-content').appendChild(pane);
            const nt = document.createElement('div'); nt.className = 'pg-tab'; nt.dataset.panel = panelId;
            nt.innerHTML = `<i class="fa-solid ${PANEL_ICONS[panelId]||'fa-cube'}"></i><span class="pg-tab-label">${PANEL_TITLES[panelId]||panelId}</span>`;
            nt.addEventListener('mousedown', e2 => startTabDrag(e2, mergeTarget.dataset.groupId, panelId));
            mergeTarget.querySelector('.pg-tabs').appendChild(nt);
            tab.remove();
            populatePaneBody(mergeTarget, panelId);
            switchTab(mergeTarget.dataset.groupId, panelId);
        } else if (cx > 0 && cy > 0 && cx < r.width && cy < r.height) {
            const nw = Math.min(400, r.width*0.4), nh = Math.min(300, r.height*0.4);
            let nx = Math.max(0, Math.min(r.width-nw, cx-nw/2));
            let ny = Math.max(0, Math.min(r.height-nh, cy-nh/2));
            const newG = { id:'g'+Date.now(), panels:[panelId], active:panelId, x:Math.round(nx), y:Math.round(ny), w:Math.round(nw), h:Math.round(nh) };
            const newEl = buildGroupEl(newG);
            const autoPane = newEl.querySelector(`.pg-pane[data-panel="${panelId}"]`);
            if (autoPane) autoPane.remove();
            const pane = el.querySelector(`.pg-pane[data-panel="${panelId}"]`);
            pane.style.display = '';
            newEl.querySelector('.pg-content').appendChild(pane);
            el.querySelector(`.pg-tab[data-panel="${panelId}"]`).remove();
            bringToFront(newEl);
        } else {
            const pane = el.querySelector(`.pg-pane[data-panel="${panelId}"]`);
            pane.style.display = '';
            el.querySelector(`.pg-tab[data-panel="${panelId}"]`).classList.add('active');
        }
        if (el.querySelectorAll('.pg-tab').length === 0) el.remove();
        savePanelLayout();
    }
    document.addEventListener('mousemove', onM); document.addEventListener('mouseup', onU);
}

// --- Content ---
function populatePaneBody(groupEl, pid) {
    const pane = groupEl.querySelector(`.pg-pane[data-panel="${pid}"]`);
    if (!pane || pane.querySelector('.fp-body')) return;
    const body = document.createElement('div'); body.className = 'fp-body'; body.id = 'fpBody_'+pid;
    if (pid === 'report') body.innerHTML = '<div class="fp-scroll" id="fpReportMarkdown"></div>';
    else if (pid === 'timeline') body.innerHTML = `<div class="fp-scroll"><div class="timeline-section"><div class="timeline-title">Chronological Activity</div><div class="timeline-subtitle">Posts over time across all targets.</div><div id="fpChrono"></div></div><hr class="timeline-divider"><div class="timeline-section"><div class="timeline-title">Pattern of Life</div><div class="timeline-subtitle">Post frequency by day &times; hour (UTC).</div><div id="fpHeatmap"></div></div></div>`;
    else if (pid === 'history') body.innerHTML = '<div class="fp-scroll" id="fpHistory"></div>';
    else if (pid === 'contacts') {
        body.innerHTML = `<div class="fp-contacts-panel"><div class="fp-contacts-toolbar"><input type="text" id="fpContactsSearch" placeholder="Filter contacts..."></div><div class="fp-graph-container" id="fpGraphContainer"></div><div class="fp-graph-resizer" id="fpGraphResizer"></div><div class="fp-contacts-list" id="fpContactsList"></div></div>`;
        const s = body.querySelector('#fpContactsSearch');
        if (s) s.addEventListener('input', e => { state.panelFilter = e.target.value.toLowerCase(); renderPanelContacts(); });
        const resizer = body.querySelector('#fpGraphResizer');
        if (resizer) initGraphResizer(resizer);
    }
    else if (pid === 'entities') body.innerHTML = '<div class="fp-scroll" id="fpEntities"></div>';
    pane.appendChild(body);
}

function populateAllPaneBodies() {
    document.querySelectorAll('.panel-group').forEach(g => {
        g.querySelectorAll('.pg-pane').forEach(pane => { populatePaneBody(g, pane.dataset.panel); });
    });
}

function setupPanelResizeObservers() {
    const tlBody = document.getElementById('fpBody_timeline');
    if (tlBody) new ResizeObserver(() => { if (state.timelineEvents?.length) updatePanelTimeline(); }).observe(tlBody);
    const gc = document.getElementById('fpGraphContainer');
    if (gc) new ResizeObserver(() => drawNetworkGraph()).observe(gc);
}

function initPanels() {
    if (panelsInited) return;
    const groups = loadSavedGroups() || getDefaultGroups();
    groups.forEach(g => buildGroupEl(g));
    populateAllPaneBodies();
    setupPanelResizeObservers();
    panelsInited = true;
}

// ═══════════════ NAVIGATION ═══════════════
function switchPage(page) {
    state.activePage = page;
    document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
    const el = document.getElementById('page-' + page);
    if (el) el.classList.add('active');
    document.querySelectorAll('.nav-item').forEach(n => n.classList.toggle('active', n.dataset.page === page));
    if (page === 'contacts') renderContacts();
    if (page === 'cache') renderCache();
    if (page === 'dashboard') renderDashboard();
    if (page === 'media') renderMedia();
}

// ═══════════════ SESSIONS ═══════════════
function renderSessions() {
    const list = document.getElementById('sessionList');
    if (!state.sessions.length) { list.innerHTML = '<div class="empty-state" style="padding:20px;"><p>No sessions yet</p></div>'; return; }
    list.innerHTML = state.sessions.map(s => {
        const color = COLORS[hashStr(s.session_id) % COLORS.length];
        const isActive = state.currentSession?.session_id === s.session_id;
        return `<div class="session-item ${isActive ? 'active' : ''}" onclick="loadSession('${s.session_id}')">
            <div class="session-dot" style="background:${color}"></div>
            <div class="session-info"><div class="session-name">${esc(s.name)}</div><div class="session-meta">${s.target_count || 0} target${(s.target_count||0)!==1?'s':''} · ${s.query_count || 0} report${(s.query_count||0)!==1?'s':''}</div></div>
            <button class="session-delete" onclick="event.stopPropagation();deleteSession('${s.session_id}')" title="Delete"><i class="fa-solid fa-xmark"></i></button>
        </div>`;
    }).join('');
    document.getElementById('statSessions').textContent = state.sessions.length;
}

async function refreshSessionList() { try { const d = await apiGet('/sessions'); state.sessions = d.sessions || []; renderSessions(); } catch (e) { toast('Could not load sessions: ' + e.message, 'error'); } }

async function loadSession(id) {
    try {
        const session = await apiGet(`/sessions/${id}`);
        state.currentSession = session;
        state.activeQueryId = null;
        renderSessions();
        await Promise.all([loadCacheStatus(), loadContacts(), loadMedia(), loadTimeline()]);
        renderTargetChips();
        if (state.runningJobId) {
            switchPage('progress');
        } else if (session.query_history?.length) {
            const latest = session.query_history[session.query_history.length - 1];
            state.activeQueryId = latest.query_id;
            viewReport(latest);
        } else {
            switchPage('report');
            document.getElementById('reportQueryText').innerHTML = '<code>Session: ' + esc(session.name) + '</code>';
            document.getElementById('reportSessionName').textContent = 'No reports yet';
            document.getElementById('reportMetaRow').innerHTML = '';
            requestAnimationFrame(() => {
                initPanels();
                const md = document.getElementById('fpReportMarkdown');
                if (md) md.innerHTML = '<div class="panel-empty" style="display:flex;flex-direction:column;align-items:center;justify-content:center;height:100%;gap:12px;"><i class="fa-solid fa-bolt" style="font-size:32px;color:var(--accent);opacity:0.6;"></i><div style="font-size:14px;color:var(--fg-muted);">Enter a query and run analysis to generate your first report.</div></div>';
                updatePanelHistory();
                renderPanelContacts();
                updatePanelEntities({});
                updatePanelTimeline();
            });
        }
    } catch (e) { toast('Failed to load session: ' + e.message, 'error'); }
}

async function deleteSession(id) {
    if (!confirm('Delete this session?')) return;
    try { await apiDel(`/sessions/${id}`); if (state.currentSession?.session_id === id) { state.currentSession = null; switchPage('dashboard'); } await refreshSessionList(); toast('Session deleted', 'info'); }
    catch (e) { toast('Delete failed: ' + e.message, 'error'); }
}

function createSession() {
    const name = document.getElementById('newSessionName').value.trim();
    const platform = document.getElementById('newSessionPlatform').value;
    const handle = document.getElementById('newSessionHandle').value.trim().replace(/^@/, '');
    const count = parseInt(document.getElementById('newSessionCount').value) || 50;
    if (!name) { toast('Session name required', 'error'); return; }
    if (!handle) { toast('Target handle required', 'error'); return; }
    apiPost('/sessions', { name, platforms: { [platform]: [handle] }, fetch_options: { default_count: count, targets: {} } })
        .then(s => { closeModal('modalNewSession'); document.getElementById('newSessionName').value = ''; document.getElementById('newSessionHandle').value = ''; refreshSessionList().then(() => loadSession(s.session_id)); toast('Session created: ' + name); })
        .catch(e => toast('Failed: ' + e.message, 'error'));
}

// ═══════════════ TARGETS ═══════════════
function openTargetsModal() { if (!state.currentSession) return; state.mtTargets = []; for (const [p, users] of Object.entries(state.currentSession.platforms || {})) for (const u of users) state.mtTargets.push({ platform: p, username: u }); renderTargetsModal(); openModal('modalTargets'); }

function renderTargetsModal() {
    const list = document.getElementById('targetsModalList');
    list.innerHTML = state.mtTargets.map((t, i) => `<div class="target-row"><span class="plat-badge">${esc(t.platform)}</span><span class="target-row-name">${esc(t.username)}</span><button class="target-row-remove" onclick="mtRemove(${i})">&times;</button></div>`).join('') || '<div style="font-size:11px;color:var(--fg-dim);padding:4px 0">No targets.</div>';
}

window.mtRemove = function(i) { state.mtTargets.splice(i, 1); renderTargetsModal(); };
window.mtAdd = function() { const p = document.getElementById('mtPlatform').value; const u = document.getElementById('mtUsername').value.trim().replace(/^@/, ''); if (!u) return; if (state.mtTargets.find(t => t.platform === p && t.username === u)) return; state.mtTargets.push({ platform: p, username: u }); document.getElementById('mtUsername').value = ''; renderTargetsModal(); };

window.mtSave = async function() {
    if (!state.mtTargets.length) { toast('Add at least one target', 'info'); return; }
    const platforms = {}; state.mtTargets.forEach(({ platform, username }) => { if (!platforms[platform]) platforms[platform] = []; platforms[platform].push(username); });
    try { const s = state.currentSession; await apiPut(`/sessions/${s.session_id}/targets`, { platforms, fetch_options: s.fetch_options }); s.platforms = platforms; closeModal('modalTargets'); await loadCacheStatus(); await refreshSessionList(); renderTargetChips(); renderPanelContacts(); if (state.activePage === 'contacts') renderContacts(); drawNetworkGraph(); toast('Targets updated'); } catch (e) { toast('Save failed: ' + e.message, 'error'); }
};

document.getElementById('btnManageTargets').addEventListener('click', openTargetsModal);

function getTargetCacheStatus(platform, username) { const entry = state.cacheEntries.find(c => c.platform === platform && c.username === username); if (!entry) return 'absent'; return entry.is_fresh ? 'fresh' : 'stale'; }

function renderTargetChips() {
    const s = state.currentSession; const bar = document.getElementById('targetChipsBar'); const container = document.getElementById('targetChips'); const queryBar = document.getElementById('queryBar');
    if (!s) { if (bar) bar.style.display = 'none'; if (queryBar) queryBar.style.display = 'none'; return; }
    if (bar) bar.style.display = ''; if (queryBar) queryBar.style.display = '';
    const targets = []; for (const [platform, users] of Object.entries(s.platforms || {})) for (const username of users) targets.push({ platform, username, status: getTargetCacheStatus(platform, username) });
    if (!container) return;
    if (!targets.length) { container.innerHTML = '<span style="font-size:11px;color:var(--fg-dim);">No targets</span>'; return; }
    container.innerHTML = targets.map(t => `<div class="target-chip" title="${esc(t.platform)}/${esc(t.username)}"><span class="chip-dot ${t.status}"></span><span class="chip-platform">${esc(t.platform)}</span><span class="chip-name">${esc(t.username)}</span><button class="chip-remove" onclick="removeTarget('${esc(t.platform)}','${esc(t.username)}')" title="Remove">&times;</button></div>`).join('');
}

async function addTargetInline() {
    const s = state.currentSession; if (!s) return; const platform = document.getElementById('addPlatformSelect').value; const username = document.getElementById('addUsernameInput').value.trim().replace(/^@/, '');
    if (!username) { toast('Enter a username', 'error'); return; }
    const updated = JSON.parse(JSON.stringify(s.platforms || {})); if (!updated[platform]) updated[platform] = []; if (updated[platform].includes(username)) { toast('Already a target', 'info'); return; } updated[platform].push(username);
    try { await apiPut(`/sessions/${s.session_id}/targets`, { platforms: updated, fetch_options: s.fetch_options }); s.platforms = updated; document.getElementById('addUsernameInput').value = ''; await loadCacheStatus(); await refreshSessionList(); renderTargetChips(); toast(`Added ${platform}/${username}`); } catch (e) { toast('Failed: ' + e.message, 'error'); }
}

async function removeTarget(platform, username) {
    const s = state.currentSession; if (!s) return; const updated = JSON.parse(JSON.stringify(s.platforms || {}));
    if (Object.values(updated).reduce((sum, arr) => sum + arr.length, 0) <= 1) { toast('Session must have at least one target', 'error'); return; }
    if (!updated[platform]) return; updated[platform] = updated[platform].filter(u => u !== username); if (!updated[platform].length) delete updated[platform];
    try { await apiPut(`/sessions/${s.session_id}/targets`, { platforms: updated, fetch_options: s.fetch_options }); s.platforms = updated; await loadCacheStatus(); await refreshSessionList();         renderTargetChips(); renderPanelContacts(); if (state.activePage === 'contacts') renderContacts(); drawNetworkGraph(); toast(`Removed ${platform}/${username}`, 'info'); } catch (e) { toast('Failed: ' + e.message, 'error'); }
}

// ═══════════════ REPORT VIEW ═══════════════
function viewReport(entry) {
    switchPage('report');
    const s = state.currentSession; const meta = entry.metadata || {}; const generated = meta.generated_utc || entry.timestamp || ''; const model = meta.models ? (meta.models.text || '') : ''; const mode = meta.mode || '';
    document.getElementById('reportQueryText').innerHTML = `<code>${esc(entry.query)}</code>`;
    document.getElementById('reportSessionName').textContent = 'Session: ' + (s?.name || 'Unknown');
    let metaHtml = `<div class="report-meta-item"><i class="fa-solid fa-clock"></i> <strong>${esc(generated)}</strong></div>`;
    if (mode) metaHtml += `<div class="report-meta-item"><i class="fa-solid fa-wifi"></i> Mode: <strong>${esc(mode)}</strong></div>`;
    if (model) metaHtml += `<div class="report-meta-item"><i class="fa-solid fa-microchip"></i> <strong>${esc(model)}</strong></div>`;
    document.getElementById('reportMetaRow').innerHTML = metaHtml;
    requestAnimationFrame(() => {
        initPanels();
        updatePanelContents(entry);
    });
}

function updatePanelContents(entry) {
    const md = document.getElementById('fpReportMarkdown');
    if (md) md.innerHTML = marked.parse(entry.report || '*(no content)*');

    updatePanelHistory();
    renderPanelContacts();
    updatePanelEntities(entry.entities || {});
    requestAnimationFrame(() => { updatePanelTimeline(); drawNetworkGraph(); });
}

function viewReportById(qid) { const s = state.currentSession; if (!s) return; const entry = s.query_history?.find(e => e.query_id === qid); if (entry) { state.activeQueryId = qid; viewReport(entry); } }

function copyReport() { const s = state.currentSession; if (!s) return; const entry = s.query_history?.find(e => e.query_id === state.activeQueryId); if (!entry) { toast('No report', 'info'); return; } navigator.clipboard.writeText(entry.report || '').then(() => toast('Copied', 'success')); }

function downloadReport() {
    const s = state.currentSession; if (!s) return; const entry = s.query_history?.find(e => e.query_id === state.activeQueryId); if (!entry) { toast('No report', 'info'); return; }
    const meta = entry.metadata || {};
    const header = `# ${entry.query}\n\n**Generated:** ${meta.generated_utc || entry.timestamp || ''}\n**Session:** ${s.name}\n**Mode:** ${meta.mode || 'N/A'}\n**Model:** ${meta.models?.text || 'N/A'}\n\n---\n\n`;
    const blob = new Blob([header + (entry.report || '')], { type: 'text/markdown' });
    const a = document.createElement('a'); a.href = URL.createObjectURL(blob); const ts = (entry.timestamp || Date.now()).replace(/[:.]/g, '-').slice(0, 19); a.download = `${s.name.replace(/[^a-zA-Z0-9]/g, '_')}_${ts}.md`;
    document.body.appendChild(a); a.click(); document.body.removeChild(a); URL.revokeObjectURL(a.href); toast('Downloaded', 'success');
}

// ═══════════════ PANEL: HISTORY ═══════════════
function updatePanelHistory() {
    const el = document.getElementById('fpHistory'); if (!el) return;
    const s = state.currentSession; if (!s) { el.innerHTML = '<div class="panel-empty">No session.</div>'; return; }
    const history = s.query_history || []; if (!history.length) { el.innerHTML = '<div class="panel-empty">No queries yet.</div>'; return; }
    el.innerHTML = [...history].reverse().map(e => `<div class="fp-history-item ${state.activeQueryId === e.query_id ? 'active' : ''}" onclick="viewReportById('${e.query_id}')"><div class="fp-history-query">${esc(e.query)}</div><div class="fp-history-ts">${fmtDate(e.timestamp)}</div></div>`).join('');
}

function initGraphResizer(handle) {
    const graph = handle.previousElementSibling;
    if (!graph) return;
    let startY, startH;
    const onMove = e => {
        const y = e.touches ? e.touches[0].clientY : e.clientY;
        const delta = y - startY;
        const nextH = Math.max(60, Math.min(startH + delta, graph.parentElement.clientHeight - 80));
        graph.style.setProperty('--graph-height', nextH + 'px');
    };
    const onUp = () => {
        document.removeEventListener('mousemove', onMove);
        document.removeEventListener('mouseup', onUp);
        document.removeEventListener('touchmove', onMove);
        document.removeEventListener('touchend', onUp);
    };
    const onDown = e => {
        e.preventDefault();
        startY = e.touches ? e.touches[0].clientY : e.clientY;
        startH = graph.getBoundingClientRect().height;
        document.addEventListener('mousemove', onMove);
        document.addEventListener('mouseup', onUp);
        document.addEventListener('touchmove', onMove, { passive: false });
        document.addEventListener('touchend', onUp);
    };
    handle.addEventListener('mousedown', onDown);
    handle.addEventListener('touchstart', onDown, { passive: false });
}

// ═══════════════ PANEL: CONTACTS ═══════════════
function renderPanelContacts() {
    const list = document.getElementById('fpContactsList'); if (!list) return;
    let contacts = state.contacts || [];
    if (state.panelFilter) contacts = contacts.filter(c => c.username.toLowerCase().includes(state.panelFilter) || c.platform.toLowerCase().includes(state.panelFilter));
    if (!contacts.length) { list.innerHTML = '<div class="panel-empty">No contacts found.</div>'; return; }
    const s = state.currentSession; const maxW = Math.max(...contacts.map(c => c.weight), 1);
    list.innerHTML = contacts.map(c => {
        const bw = Math.max(8, Math.round(c.weight / maxW * 100));
        const itypes = (c.interaction_types || []).map(t => `<span class="fp-itype ${t}">${esc(t.replace('_',' '))}</span>`).join('');
        const inSession = s && (s.platforms || {})[c.platform]?.includes(c.username);
        const btn = inSession
            ? `<button class="fp-contact-add in-session" disabled><i class="fa-solid fa-check"></i></button>`
            : `<button class="fp-contact-add" onclick="addContactToSession('${esc(c.platform)}','${esc(c.username)}')"><i class="fa-solid fa-plus"></i></button>`;
        return `<div class="fp-contact-row"><div class="fp-contact-avatar">${esc(c.username.slice(0,2).toUpperCase())}</div><div class="fp-contact-info"><div class="fp-contact-name">${esc(c.username)}</div><div class="fp-contact-detail"><span class="fp-plat-badge">${esc(c.platform)}</span> ${itypes}</div></div><div class="fp-contact-weight"><div class="weight-bar-wrap"><div class="weight-bar" style="width:${bw}%"></div></div><span class="weight-num">${c.weight}</span></div>${btn}</div>`;
    }).join('');
}

// ═══════════════ PANEL: ENTITIES ═══════════════
function updatePanelEntities(entities) {
    const c = document.getElementById('fpEntities'); if (!c) return;
    const nonEmpty = Object.keys(entities || {}).filter(k => entities[k]?.length);
    if (!nonEmpty.length) { c.innerHTML = '<div class="panel-empty">No entities extracted.</div>'; return; }
    c.innerHTML = nonEmpty.map(k => `<div class="fp-entity-group"><div class="fp-entity-title">${esc(k)}</div>${entities[k].map(v => `<span class="fp-entity-pill">${esc(v)}</span>`).join('')}</div>`).join('');
}

// ═══════════════ PANEL: TIMELINE ═══════════════
function updatePanelTimeline() { const events = state.timelineEvents || []; renderChrono(events); renderHeatmapPanel(events); }

function renderChrono(events) {
    const el = document.getElementById('fpChrono'); if (!el) return; el.innerHTML = '';
    if (!events.length) { el.innerHTML = '<div class="panel-empty" style="padding:16px 0">No timeline data.</div>'; return; }
    const formatDate = d3.timeFormat('%Y-%m-%d');
    const daily = {}; events.forEach(e => { const d = formatDate(new Date(e.timestamp)); daily[d] = (daily[d] || 0) + 1; });
    const data = Object.keys(daily).map(d => ({ date: d3.timeParse('%Y-%m-%d')(d), count: daily[d] })).sort((a, b) => a.date - b.date);
    const W = el.clientWidth || 400, H = 140; if (W < 50) return;
    const m = { top: 8, right: 12, bottom: 22, left: 28 }; const w = W - m.left - m.right, h = H - m.top - m.bottom; if (w < 30 || h < 20) return;
    const x = d3.scaleTime().domain(d3.extent(data, d => d.date)).range([0, w]); const y = d3.scaleLinear().domain([0, d3.max(data, d => d.count)]).range([h, 0]);
    const svg = d3.select(el).append('svg').attr('width', W).attr('height', H).append('g').attr('transform', `translate(${m.left},${m.top})`);
    svg.append('g').attr('class', 'axis').attr('transform', `translate(0,${h})`).call(d3.axisBottom(x).ticks(5));
    svg.append('g').attr('class', 'axis').call(d3.axisLeft(y).ticks(4).tickFormat(d3.format('d')));
    const bw = Math.max(2, w / data.length - 1); const tooltip = d3.select('#d3Tooltip');
    svg.selectAll('.bar').data(data).enter().append('rect').attr('class', 'bar').attr('x', d => x(d.date) - bw/2).attr('y', d => y(d.count)).attr('width', bw).attr('height', d => h - y(d.count))
        .on('mouseover', (ev, d) => { tooltip.transition().duration(100).style('opacity', 1); tooltip.html(`<strong>${formatDate(d.date)}</strong><br>${d.count} posts`).style('left', (ev.clientX-30)+'px').style('top', (ev.clientY-48)+'px'); })
        .on('mouseout', () => tooltip.transition().duration(200).style('opacity', 0));
}

function renderHeatmapPanel(events) {
    const el = document.getElementById('fpHeatmap'); if (!el) return; el.innerHTML = '';
    if (!events.length) { el.innerHTML = '<div class="panel-empty" style="padding:16px 0">No activity data.</div>'; return; }
    const matrix = Array.from({ length: 7 }, () => Array(24).fill(0)); let mx = 1;
    events.forEach(e => { const dt = new Date(e.timestamp); matrix[dt.getUTCDay()][dt.getUTCHours()]++; if (matrix[dt.getUTCDay()][dt.getUTCHours()] > mx) mx = matrix[dt.getUTCDay()][dt.getUTCHours()]; });
    const days = ['Sun','Mon','Tue','Wed','Thu','Fri','Sat'];
    let heatR='0',heatG='229',heatB='155';
    try { const tmp = document.createElement('div'); tmp.style.color = getCssVar('--accent'); document.body.appendChild(tmp); const c = getComputedStyle(tmp).color; document.body.removeChild(tmp); const m = c.match(/\d+/g); if (m) { heatR=m[0]; heatG=m[1]; heatB=m[2]; } } catch {}
    let html = `<div class="heatmap-grid"><div style="grid-column:2/26;display:flex;justify-content:space-between;font-size:9px;color:var(--fg-dim);margin-bottom:4px"><span>00:00</span><span>12:00</span><span>23:00</span></div>`;
    for (let d = 0; d < 7; d++) { html += `<div class="heatmap-label">${days[d]}</div>`; for (let h = 0; h < 24; h++) { const v = matrix[d][h]; const op = v === 0 ? 0.05 : Math.max(0.15, v / mx); html += `<div class="heatmap-cell" style="background:rgba(${heatR},${heatG},${heatB},${op})" title="${days[d]} ${String(h).padStart(2,'0')}:00 — ${v}"></div>`; } }
    html += '</div>'; el.innerHTML = html;
}

// ═══════════════ D3 NETWORK GRAPH ═══════════════
let graphZoom = null;

function drawNetworkGraph() {
    const container = document.getElementById('fpGraphContainer'); if (!container) return;
    container.querySelectorAll('svg').forEach(s => s.remove());
    container.querySelectorAll('.graph-empty,.graph-zoom-ctrl').forEach(e => e.remove());
    const s = state.currentSession; const contacts = state.contacts || [];
    if (!s || !contacts.length) { container.innerHTML = '<div class="graph-empty"><i class="fa-solid fa-diagram-project"></i><span>Run analysis to build graph</span></div>'; return; }
    const W = container.clientWidth || 240, H = container.clientHeight || 160; if (W < 30 || H < 30) return;
    const accentColor = getCssVar('--accent'), borderMedium = getCssVar('--border-light'), bgRaised = getCssVar('--bg-card-hover'), bgSurface = getCssVar('--bg-card'), textSec = getCssVar('--fg-muted');
    const sourceNodes = []; for (const [platform, users] of Object.entries(s.platforms || {})) for (const u of users) sourceNodes.push({ id: `${platform}/${u}`, label: u, type: 'source' });
    const allIds = new Set(sourceNodes.map(n => n.id));
    const contactNodes = contacts.slice(0, 30).map(c => ({ id: `${c.platform}/${c.username}`, label: c.username, type: 'contact', weight: c.weight })).filter(n => !allIds.has(n.id));
    const nodes = [...sourceNodes, ...contactNodes]; const validIds = new Set(nodes.map(n => n.id));
    const maxW = Math.max(...contacts.map(c => c.weight), 1);
    const links = []; contacts.slice(0, 30).forEach(c => { const cId = `${c.platform}/${c.username}`; sourceNodes.forEach(src => { if (src.id.split('/')[0] === c.platform && validIds.has(cId)) links.push({ source: src.id, target: cId, weight: c.weight }); }); });
    const validLinks = links.filter(l => validIds.has(l.source) && validIds.has(l.target));

    const svg = d3.select(container).append('svg').attr('width', W).attr('height', H);
    const zoomG = svg.append('g');
    const zoomBehavior = d3.zoom().scaleExtent([0.3, 5]).on('zoom', ev => zoomG.attr('transform', ev.transform));
    svg.call(zoomBehavior);
    svg.on('dblclick.zoom', null);
    graphZoom = { behavior: zoomBehavior, svg, zoomG };

    const sim = d3.forceSimulation(nodes).force('link', d3.forceLink(validLinks).id(d => d.id).distance(50).strength(0.5)).force('charge', d3.forceManyBody().strength(-80)).force('center', d3.forceCenter(W/2, H/2)).force('collision', d3.forceCollide(14));
    const link = zoomG.append('g').selectAll('line').data(validLinks).join('line').attr('class', 'graph-link').style('stroke-width', d => 0.5 + (d.weight/maxW)*1.8);
    const node = zoomG.append('g').selectAll('g').data(nodes).join('g').attr('class', d => d.type === 'source' ? 'node-source' : 'node-contact')
        .call(d3.drag().on('start', (ev, d) => { const t = d3.zoomTransform(svg.node()); if (!ev.active) sim.alphaTarget(0.3).restart(); d.fx = (ev.x - t.x) / t.k; d.fy = (ev.y - t.y) / t.k; }).on('drag', (ev, d) => { const t = d3.zoomTransform(svg.node()); d.fx = (ev.x - t.x) / t.k; d.fy = (ev.y - t.y) / t.k; }).on('end', (ev, d) => { if (!ev.active) sim.alphaTarget(0); d.fx = null; d.fy = null; }));
    node.append('circle').attr('r', d => d.type === 'source' ? 9 : 5 + (d.weight||1)/maxW*4).style('fill', d => d.type === 'source' ? accentColor : bgRaised).style('stroke', d => d.type === 'source' ? bgSurface : borderMedium).style('stroke-width', d => d.type === 'source' ? '2.5px' : '1.5px');
    node.append('text').attr('class', 'node-label').attr('dy', d => -(d.type==='source'?12:10)).attr('text-anchor', 'middle').style('fill', textSec).text(d => d.label.length > 11 ? d.label.slice(0,10)+'\u2026' : d.label);
    sim.on('tick', () => { link.attr('x1', d => d.source.x).attr('y1', d => d.source.y).attr('x2', d => d.target.x).attr('y2', d => d.target.y); node.attr('transform', d => `translate(${d.x},${d.y})`); });

    const ctrl = document.createElement('div'); ctrl.className = 'graph-zoom-ctrl';
    ctrl.innerHTML = `<button class="gz-btn" onclick="graphZoomIn()" title="Zoom in"><i class="fa-solid fa-plus"></i></button><button class="gz-btn" onclick="graphZoomOut()" title="Zoom out"><i class="fa-solid fa-minus"></i></button><button class="gz-btn" onclick="graphZoomReset()" title="Reset view"><i class="fa-solid fa-expand"></i></button><button class="gz-btn" onclick="graphSpread()" title="Spread nodes"><i class="fa-solid fa-arrows-up-down-left-right"></i></button>`;
    container.appendChild(ctrl);
}

function graphZoomIn() { if (!graphZoom) return; graphZoom.svg.transition().duration(250).call(graphZoom.behavior.scaleBy, 1.4); }
function graphZoomOut() { if (!graphZoom) return; graphZoom.svg.transition().duration(250).call(graphZoom.behavior.scaleBy, 1/1.4); }
function graphZoomReset() { if (!graphZoom) return; graphZoom.svg.transition().duration(350).call(graphZoom.behavior.transform, d3.zoomIdentity); }
function graphSpread() { if (!graphZoom) return; graphZoom.svg.transition().duration(350).call(graphZoom.behavior.transform, d3.zoomIdentity); drawNetworkGraph(); }

// ═══════════════ DASHBOARD ═══════════════
function renderDashboard() {
    const allReports = [];
    (state.sessions || []).forEach(s => { (s.recent_queries || s.query_history || []).forEach(q => { allReports.push({ ...q, sessionName: s.name, sessionId: s.session_id }); }); });
    allReports.sort((a, b) => (b.timestamp || '').localeCompare(a.timestamp || ''));
    document.getElementById('statReports').textContent = allReports.length;
    document.getElementById('statContacts').textContent = state.contacts.length;
    document.getElementById('statCache').textContent = state.cacheEntries.length;
    const list = document.getElementById('recentReportsList');
    if (!allReports.length) { list.innerHTML = '<div class="empty-state"><i class="fa-solid fa-file-lines"></i><p>No reports yet.</p></div>'; }
    else { list.innerHTML = allReports.slice(0, 10).map(r => `<div class="report-list-item" onclick="loadSession('${r.sessionId}').then(()=>viewReportById('${r.query_id}'))"><div class="rli-icon platform-icon"><i class="fa-solid fa-feather"></i></div><div class="rli-info"><div class="rli-query">${esc(r.query)}</div><div class="rli-meta">${esc(r.sessionName)} · ${fmtDate(r.timestamp)}</div></div></div>`).join(''); }
    const sList = document.getElementById('dashSessionList'); if (!sList) return;
    if (!state.sessions.length) { sList.innerHTML = '<div class="empty-state" style="padding:30px 10px;"><i class="fa-solid fa-folder-open" style="font-size:28px;"></i><p>No sessions yet.</p></div>'; }
    else { sList.innerHTML = state.sessions.map(s => { const targets = []; for (const [p, users] of Object.entries(s.platforms || {})) for (const u of users) targets.push({ platform: p, username: u }); return `<div class="dash-session-card" onclick="loadSession('${s.session_id}')"><div class="dsc-name">${esc(s.name)}</div><div class="dsc-meta">${targets.length} target${targets.length!==1?'s':''} · ${s.query_count||0} report${(s.query_count||0)!==1?'s':''}</div><div class="dsc-targets">${targets.slice(0,5).map(t=>`<span class="dsc-target">${esc(t.platform)}/${esc(t.username)}</span>`).join('')}${targets.length>5?`<span class="dsc-target">+${targets.length-5}</span>`:''}</div></div>`; }).join(''); }
}

// ═══════════════ ANALYSIS ═══════════════
function runAnalysis() {
    if (!state.currentSession) { toast('Select or create a session first', 'error'); return; }
    const s = state.currentSession; const qi = document.getElementById('queryInput'); const query = (qi ? qi.value : '').trim();
    if (!query) { toast('Enter an analysis query', 'error'); qi && qi.focus(); return; }
    if (qi) { qi.value = ''; qi.disabled = true; } const runBtn = document.getElementById('btnRunAnalysis'); if (runBtn) runBtn.disabled = true;
    setStatus(true, 'Starting...'); switchPage('progress'); document.getElementById('progressLog').innerHTML = ''; document.getElementById('progressStageLabel').textContent = 'Starting analysis...';
    apiPut(`/sessions/${s.session_id}/targets`, { platforms: s.platforms, fetch_options: s.fetch_options })
        .then(() => apiPost(`/sessions/${s.session_id}/analyse`, { query, force_refresh: false }))
        .then(job => { state.runningJobId = job.job_id; startProgressStream(job.job_id, query); })
        .catch(e => { if (e.message.includes('already has a running')) toast('Analysis already running', 'info'); else toast('Failed: ' + e.message, 'error'); setStatus(false); switchPage('dashboard'); })
        .finally(() => { if (qi) qi.disabled = false; if (runBtn) runBtn.disabled = false; });
}

function startProgressStream(jobId, query) {
    appendLog('info', `Query: ${query}`); if (state.sseSource) state.sseSource.close();
    const es = new EventSource(`${API}/jobs/${jobId}/stream`); state.sseSource = es;
    ['stage','log','status','complete','error'].forEach(t => { es.addEventListener(t, e => { try { handleJobEvent(t, JSON.parse(e.data), jobId); } catch {} }); });
    es.onerror = () => { es.close(); pollJob(jobId); };
}

function handleJobEvent(type, data) {
    if (type === 'stage') { document.getElementById('progressStageLabel').textContent = data.message || ''; appendLog('stage', `\u25b6 ${data.message}`); }
    else if (type === 'log' || type === 'status') appendLog('info', data.message);
    else if (type === 'complete') { appendLog('complete', '\u2713 Analysis complete'); finishAnalysis(); }
    else if (type === 'error') { appendLog('error', `\u2717 ${data.message}`); setStatus(false); toast('Error: ' + data.message, 'error'); }
}

function appendLog(cls, msg) {
    const log = document.getElementById('progressLog'); const line = document.createElement('span'); line.className = `log-line ${cls}`; line.textContent = msg; line.style.display = 'block';
    if (cls === 'stage') line.style.color = 'var(--accent)'; if (cls === 'error') line.style.color = 'var(--danger)'; if (cls === 'complete') line.style.color = 'var(--accent)';
    log.appendChild(line); log.appendChild(document.createElement('br')); log.scrollTop = log.scrollHeight;
}

async function finishAnalysis() {
    if (state.sseSource) { state.sseSource.close(); state.sseSource = null; } setStatus(false);
    try {
        const session = await apiGet(`/sessions/${state.currentSession.session_id}`); state.currentSession = session;
        await refreshSessionList(); await loadCacheStatus(); await loadContacts(); await loadMedia(); await loadTimeline(); renderTargetChips();
        if (session.query_history?.length) { const latest = session.query_history[session.query_history.length - 1]; state.activeQueryId = latest.query_id; viewReport(latest); }
        toast('Analysis complete');
    } catch (e) { toast('Could not reload: ' + e.message, 'error'); }
}

async function pollJob(jobId) {
    const poll = async () => { try { const job = await apiGet(`/jobs/${jobId}`); if (job.status === 'complete') { finishAnalysis(); return; } if (job.status === 'error') { toast('Failed: ' + job.error, 'error'); setStatus(false); return; } if (job.progress?.message) document.getElementById('progressStageLabel').textContent = job.progress.message; setTimeout(poll, 2000); } catch { setTimeout(poll, 3000); } }; poll();
}

// ═══════════════ CONTACTS (Full Page) ═══════════════
async function loadContacts() { const s = state.currentSession; if (!s) { state.contacts = []; return; } try { const d = await apiGet(`/sessions/${s.session_id}/contacts`); state.contacts = d.contacts || []; state.contactsDismissed = d.dismissed || []; document.getElementById('contactCount').textContent = state.contacts.length; document.getElementById('statContacts').textContent = state.contacts.length; } catch { state.contacts = []; } }

function renderContacts(filter) {
    filter = filter || state.contactsFilter; state.contactsFilter = filter.toLowerCase();
    let contacts = state.contacts; if (state.contactsFilter) contacts = contacts.filter(c => c.username.toLowerCase().includes(state.contactsFilter) || c.platform.toLowerCase().includes(state.contactsFilter));
    if (!contacts.length) { document.getElementById('contactsGrid').innerHTML = `<div class="empty-state" style="grid-column:1/-1;"><i class="fa-solid fa-user-slash"></i><p>${filter ? 'No matches.' : 'No contacts yet.'}</p></div>`; return; }
    const s = state.currentSession;
    document.getElementById('contactsGrid').innerHTML = contacts.map(c => {
        const initials = c.username.slice(0, 2).toUpperCase(); const color = COLORS[hashStr(c.platform + c.username) % COLORS.length];
        const itypes = (c.interaction_types || []).map(t => `<span class="tag platform" style="font-size:9px;">${esc(t.replace('_',' '))}</span>`).join(' ');
        const inSession = s && (s.platforms || {})[c.platform]?.includes(c.username);
        const actionBtn = inSession
            ? `<button class="topbar-btn" style="flex:1;justify-content:center;opacity:0.5;cursor:default;" disabled><i class="fa-solid fa-check"></i> In Session</button>`
            : `<button class="topbar-btn" style="flex:1;justify-content:center;" onclick="addContactToSession('${esc(c.platform)}','${esc(c.username)}')"><i class="fa-solid fa-plus"></i> Add to Session</button>`;
        return `<div class="contact-card"><div class="contact-card-header"><div class="contact-avatar" style="background:${color}22;color:${color};border:1.5px solid ${color}44;">${initials}</div><div><div class="ch-name">${esc(c.username)}</div><div class="ch-handle">${esc(c.platform)}</div></div></div><div class="contact-details"><div class="contact-detail"><i class="fa-solid fa-arrows-left-right"></i> Weight: ${c.weight}</div><div class="contact-detail"><i class="fa-solid fa-tag"></i> ${itypes || '<span style="color:var(--fg-dim)">None</span>'}</div>${c.first_seen ? `<div class="contact-detail"><i class="fa-solid fa-calendar"></i> First: ${fmtDate(c.first_seen)}</div>` : ''}${c.last_seen ? `<div class="contact-detail"><i class="fa-solid fa-clock"></i> Last: ${fmtDate(c.last_seen)}</div>` : ''}</div><div class="contact-card-footer">${actionBtn}<button class="topbar-btn" style="color:var(--danger);border-color:var(--border);" onclick="dismissContact('${esc(c.platform)}','${esc(c.username)}')"><i class="fa-solid fa-eye-slash"></i></button></div></div>`;
    }).join('');
}

function filterContacts(val) { renderContacts(val); }

function updateContactCardSize(val) {
    const grid = document.getElementById('contactsGrid');
    const label = document.getElementById('contactCardSizeLabel');
    if (grid) grid.style.setProperty('--card-min-width', val + 'px');
    if (label) label.textContent = val;
    try { localStorage.setItem('osint_contact_card_size', val); } catch {}
}

function restoreContactCardSize() {
    try {
        const saved = localStorage.getItem('osint_contact_card_size');
        if (saved) {
            const slider = document.getElementById('contactCardSize');
            if (slider) { slider.value = saved; updateContactCardSize(saved); }
        }
    } catch {}
}

async function dismissContact(platform, username) { const s = state.currentSession; if (!s) return; try { await apiPost(`/sessions/${s.session_id}/contacts/dismiss`, { platform, username }); toast(`Dismissed ${username}`, 'info'); await loadContacts(); renderContacts(); renderPanelContacts(); } catch (e) { toast('Failed: ' + e.message, 'error'); } }

async function addContactToSession(platform, username) {
    const s = state.currentSession; if (!s) return;
    const updated = JSON.parse(JSON.stringify(s.platforms || {})); if (!updated[platform]) updated[platform] = []; if (updated[platform].includes(username)) { toast('Already in session', 'info'); return; } updated[platform].push(username);
    try { await apiPut(`/sessions/${s.session_id}/targets`, { platforms: updated, fetch_options: s.fetch_options }); s.platforms = updated; await loadCacheStatus(); await refreshSessionList(); renderTargetChips(); if (state.activePage === 'contacts') renderContacts(); renderPanelContacts(); drawNetworkGraph(); toast(`Added ${platform}/${username}`); } catch (e) { toast('Failed: ' + e.message, 'error'); }
}

// ═══════════════ CACHE ═══════════════
async function loadCacheStatus() { try { const d = await apiGet('/cache'); state.cacheEntries = d.entries || []; document.getElementById('cacheCount').textContent = state.cacheEntries.length; } catch { state.cacheEntries = []; } }

function renderCache() {
    const filter = state.cacheFilter; const filtered = filter === 'all' ? state.cacheEntries : state.cacheEntries.filter(c => filter === 'fresh' ? c.is_fresh : !c.is_fresh);
    document.getElementById('cacheTotal').textContent = state.cacheEntries.length; document.getElementById('cacheHits').textContent = state.cacheEntries.filter(c => c.is_fresh).length; document.getElementById('cacheExpired').textContent = state.cacheEntries.filter(c => !c.is_fresh).length; document.getElementById('cacheCount').textContent = state.cacheEntries.length;
    if (!filtered.length) { document.getElementById('cacheList').innerHTML = `<div class="empty-state"><i class="fa-solid fa-database"></i><p>${filter !== 'all' ? 'No matches.' : 'Cache is empty.'}</p></div>`; return; }
    document.getElementById('cacheList').innerHTML = filtered.map(c => `<div class="cache-item"><div class="ci-status ${c.is_fresh?'fresh':'stale'}"></div><div class="ci-info"><div class="ci-key">${esc(c.platform)} / ${esc(c.username)}</div><div class="ci-meta">${c.post_count} posts · ${c.media_found||0} media · ${c.media_analyzed||0} analyzed · ${fmtAge(c.cached_at)}</div></div><div class="ci-actions"><button class="ci-btn danger" onclick="deleteCacheEntry('${esc(c.platform)}_${esc(c.username)}')"><i class="fa-solid fa-trash"></i></button></div></div>`).join('');
}

function filterCache(filter) { state.cacheFilter = filter; renderCache(); }
async function deleteCacheEntry(key) { if (!confirm('Delete?')) return; try { await apiPost('/cache/purge', { targets: ['specific'], keys: [key] }); await loadCacheStatus(); renderCache(); toast('Removed', 'info'); } catch (e) { toast('Failed: ' + e.message, 'error'); } }
window.cachePurgeAll = async function() { if (!confirm('Purge all?')) return; try { await apiPost('/cache/purge', { targets: ['all'] }); await loadCacheStatus(); renderCache(); toast('Purged'); } catch (e) { toast('Failed: ' + e.message, 'error'); } };
window.cachePurgeSelected = async function() { const keys = Array.from(document.querySelectorAll('.cache-cb:checked')).map(cb => cb.value); if (!keys.length) return; if (!confirm(`Delete ${keys.length}?`)) return; try { await apiPost('/cache/purge', { targets: ['specific'], keys }); await loadCacheStatus(); renderCache(); toast(`Purged ${keys.length}`); } catch (e) { toast('Failed: ' + e.message, 'error'); } };

// ═══════════════ MEDIA (Full Page) ═══════════════
async function loadMedia() { const s = state.currentSession; if (!s) { state.mediaItems = []; return; } try { const res = await apiGet(`/sessions/${s.session_id}/media`); state.mediaItems = res.media || []; document.getElementById('mediaCount').textContent = state.mediaItems.length; } catch { state.mediaItems = []; } }

function renderMedia() {
    const grid = document.getElementById('mediaGrid'); const s = state.currentSession;
    if (!state.mediaItems.length) { grid.innerHTML = '<div class="empty-state"><i class="fa-solid fa-images"></i><p>No media files.</p></div>'; return; }
    grid.innerHTML = state.mediaItems.map(m => { const url = `/api/v1/sessions/${s.session_id}/media/file?path=${encodeURIComponent(m.path)}`; return `<div class="media-item-container"><a href="${esc(url)}" target="_blank"><img src="${esc(url)}" class="media-item" loading="lazy" alt="Media"></a><div class="media-analysis-overlay">${esc(m.analysis || 'No analysis')}</div></div>`; }).join('');
}

// ═══════════════ TIMELINE DATA ═══════════════
async function loadTimeline() { const s = state.currentSession; if (!s) { state.timelineEvents = []; return; } try { const d = await apiGet(`/sessions/${s.session_id}/timeline`); state.timelineEvents = d.events || []; } catch { state.timelineEvents = []; } }

// ═══════════════ GLOBAL SEARCH ═══════════════
function handleGlobalSearch(val) {
    if (!val.trim()) return; const q = val.toLowerCase();
    for (const s of state.sessions) { const queries = s.recent_queries || s.query_history || []; for (const qe of queries) { if (qe.query.toLowerCase().includes(q)) { loadSession(s.session_id).then(() => viewReportById(qe.query_id)); return; } } }
    const con = state.contacts.find(c => c.username.toLowerCase().includes(q) || c.platform.toLowerCase().includes(q));
    if (con) { switchPage('contacts'); document.getElementById('contactSearch').value = val; filterContacts(val); }
}

// ═══════════════ MODALS ═══════════════
document.querySelectorAll('.modal-overlay').forEach(m => { m.addEventListener('click', e => { if (e.target === m) m.classList.remove('show'); }); });
document.addEventListener('keydown', e => { if (e.key === 'Escape') document.querySelectorAll('.modal-overlay.show').forEach(m => m.classList.remove('show')); });

// ═══════════════ INIT ═══════════════
(async function init() {
    const saved = localStorage.getItem('osint-agent-theme'); applyTheme(saved || 'dark');
    restoreContactCardSize();
    const qi = document.getElementById('queryInput'); if (qi) qi.addEventListener('keydown', e => { if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') runAnalysis(); });
    const tc = document.getElementById('targetChipsBar'); if (tc) tc.style.display = 'none';
    const qb = document.getElementById('queryBar'); if (qb) qb.style.display = 'none';
    await refreshSessionList();
    await loadCacheStatus();
    renderDashboard();
})();
