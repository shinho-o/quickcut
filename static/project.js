// QuickCut — 프로젝트 편집 뷰

const proj = window.PROJECT;

const clipsRoot = document.getElementById('clipsRoot');
const analyzeBtn = document.getElementById('analyzeBtn');
const analyzeStatus = document.getElementById('analyzeStatus');
const exportBtn = document.getElementById('exportBtn');
const exportStatus = document.getElementById('exportStatus');
const stylePreset = document.getElementById('stylePreset');
const orientation = document.getElementById('orientation');
const skipSilence = document.getElementById('skipSilence');
const smartCrop = document.getElementById('smartCrop');
const autoHighlight = document.getElementById('autoHighlight');
const highlightDuration = document.getElementById('highlightDuration');
const highlightDurField = document.getElementById('highlightDurField');
const smartEdit = document.getElementById('smartEdit');
const smartEditOpts = document.getElementById('smartEditOpts');
const removeFillers = document.getElementById('removeFillers');
const aggressiveFillers = document.getElementById('aggressiveFillers');
const jumpGap = document.getElementById('jumpGap');
const titleInput = document.getElementById('title');

// ───── 유틸 ─────

function fmt(sec) {
    sec = Math.max(0, sec);
    const m = Math.floor(sec / 60);
    const s = (sec - m * 60).toFixed(1);
    return `${m}:${s.padStart(4, '0')}`;
}

async function saveMeta(patch) {
    await fetch(`/project/${proj.id}/update`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(patch),
    });
}

// ───── 설정 저장 ─────

stylePreset.addEventListener('change', () =>
    saveMeta({ style_preset: stylePreset.value }));
orientation.addEventListener('change', () =>
    saveMeta({ orientation: orientation.value }));
skipSilence.addEventListener('change', () =>
    saveMeta({ skip_silence: skipSilence.checked }));
if (smartCrop) smartCrop.addEventListener('change', () =>
    saveMeta({ smart_crop: smartCrop.checked }));
if (autoHighlight) autoHighlight.addEventListener('change', () => {
    saveMeta({ auto_highlight: autoHighlight.checked });
    if (highlightDurField)
        highlightDurField.style.display = autoHighlight.checked ? '' : 'none';
});
if (highlightDuration) highlightDuration.addEventListener('change', () =>
    saveMeta({ highlight_duration: parseInt(highlightDuration.value) || 60 }));

if (smartEdit) smartEdit.addEventListener('change', () => {
    saveMeta({ smart_edit: smartEdit.checked });
    if (smartEditOpts)
        smartEditOpts.style.display = smartEdit.checked ? '' : 'none';
});
if (removeFillers) removeFillers.addEventListener('change', () =>
    saveMeta({ remove_fillers: removeFillers.checked }));
if (aggressiveFillers) aggressiveFillers.addEventListener('change', () =>
    saveMeta({ aggressive_fillers: aggressiveFillers.checked }));
if (jumpGap) jumpGap.addEventListener('change', () =>
    saveMeta({ jump_gap: parseFloat(jumpGap.value) || 0.4 }));

titleInput.addEventListener('change', () => {
    const v = titleInput.value.trim();
    if (v) saveMeta({ title: v });
});
titleInput.addEventListener('keydown', e => {
    if (e.key === 'Enter') { e.preventDefault(); titleInput.blur(); }
});

// ───── mm:ss.s 시간 포맷 ↔ 초 변환 ─────
function parseTime(str) {
    if (typeof str === 'number') return str;
    const s = String(str).trim();
    const m = s.match(/^(?:(\d+):)?(\d+(?:\.\d+)?)$/);
    if (!m) return parseFloat(s) || 0;
    const mins = parseFloat(m[1] || 0);
    const secs = parseFloat(m[2] || 0);
    return mins * 60 + secs;
}
function fmtTime(sec) {
    sec = Math.max(0, sec || 0);
    const m = Math.floor(sec / 60);
    const s = (sec - m * 60).toFixed(1);
    return `${m}:${s.padStart(4, '0')}`;
}

// ───── Undo 스택 ─────
const UNDO_MAX = 20;
const undoStack = [];
function pushUndo(entry) {
    undoStack.push(entry);
    if (undoStack.length > UNDO_MAX) undoStack.shift();
}
function showToast(msg, onUndo) {
    let toast = document.getElementById('toast');
    if (!toast) {
        toast = document.createElement('div');
        toast.id = 'toast';
        document.body.appendChild(toast);
    }
    toast.innerHTML = `<span>${msg}</span>`;
    if (onUndo) {
        const btn = document.createElement('button');
        btn.textContent = '되돌리기';
        btn.className = 'toast-undo';
        btn.onclick = () => { onUndo(); toast.classList.remove('show'); };
        toast.appendChild(btn);
    }
    toast.classList.add('show');
    clearTimeout(toast._timer);
    toast._timer = setTimeout(() => toast.classList.remove('show'), 6000);
}
// 키보드 Ctrl+Z
window.addEventListener('keydown', e => {
    if ((e.ctrlKey || e.metaKey) && e.key === 'z' && undoStack.length) {
        e.preventDefault();
        const f = undoStack.pop();
        if (f) f();
    }
});

// ───── 클립 렌더 ─────

function renderClip(clip) {
    const card = document.createElement('div');
    card.className = 'clip-card';
    card.dataset.id = clip.id;
    card.innerHTML = `
        <div class="clip-head">
            <div class="clip-title">${clip.filename}</div>
            <div class="clip-meta">
                ${clip.width}×${clip.height} · ${fmt(clip.duration)}
            </div>
        </div>
        <div class="clip-body">
            <div>
                <div class="video-wrap">
                    <video src="/project/${proj.id}/clip/${clip.id}/video" controls preload="metadata"></video>
                </div>
                <div class="trim">
                    <div class="trim-track" data-trim>
                        <div class="trim-range"></div>
                        <div class="trim-handle" data-handle="start"></div>
                        <div class="trim-handle" data-handle="end"></div>
                    </div>
                    <div class="trim-labels">
                        <span class="t-start">0:00.0</span>
                        <span class="t-end">${fmt(clip.duration)}</span>
                    </div>
                </div>
                <div class="silence">
                    <h4>무음 구간 <span class="hint-inline"></span></h4>
                    <div class="sil-list"></div>
                </div>
            </div>
            <div class="captions">
                <h4>자막</h4>
                <div class="cap-list"></div>
                <button class="btn-sec add-cap" type="button">+ 자막 줄 추가</button>
            </div>
        </div>
    `;
    clipsRoot.appendChild(card);

    initTrim(card, clip);
    renderCaptions(card, clip);
    renderSilence(card, clip);
}

// ───── 트림 핸들 ─────

function initTrim(card, clip) {
    const track = card.querySelector('.trim-track');
    const range = card.querySelector('.trim-range');
    const h1 = card.querySelector('[data-handle=start]');
    const h2 = card.querySelector('[data-handle=end]');
    const t0Label = card.querySelector('.t-start');
    const t1Label = card.querySelector('.t-end');

    function layout() {
        const dur = clip.duration || 1;
        const p0 = (clip.trim_start / dur) * 100;
        const p1 = (clip.trim_end / dur) * 100;
        range.style.left = p0 + '%';
        range.style.width = (p1 - p0) + '%';
        h1.style.left = p0 + '%';
        h2.style.left = p1 + '%';
        t0Label.textContent = fmt(clip.trim_start);
        t1Label.textContent = fmt(clip.trim_end);
    }
    layout();

    function bindDrag(handle, key) {
        handle.addEventListener('mousedown', e => {
            e.preventDefault();
            const before = clip[key];

            const onMove = ev => {
                const rect = track.getBoundingClientRect();
                const x = Math.max(0, Math.min(1, (ev.clientX - rect.left) / rect.width));
                const val = x * clip.duration;
                if (key === 'trim_start') {
                    clip.trim_start = Math.min(val, clip.trim_end - 0.5);
                } else {
                    clip.trim_end = Math.max(val, clip.trim_start + 0.5);
                }
                layout();
            };
            const onUp = () => {
                window.removeEventListener('mousemove', onMove);
                window.removeEventListener('mouseup', onUp);
                if (Math.abs(clip[key] - before) < 0.05) return;
                const after = clip[key];
                saveMeta({ clips: [{ id: clip.id, [key]: after }] });
                pushUndo(() => {
                    clip[key] = before;
                    layout();
                    saveMeta({ clips: [{ id: clip.id, [key]: before }] });
                    showToast('트림 되돌림');
                });
                showToast(`트림 조정 (Ctrl+Z 되돌리기)`);
            };
            window.addEventListener('mousemove', onMove);
            window.addEventListener('mouseup', onUp);
        });
    }
    bindDrag(h1, 'trim_start');
    bindDrag(h2, 'trim_end');
}

// ───── 자막 편집 ─────

function renderCaptions(card, clip) {
    const list = card.querySelector('.cap-list');
    list.innerHTML = '';

    if (!clip.segments || !clip.segments.length) {
        list.innerHTML = '<div class="empty-hint">자동 분석을 실행하면 자막이 채워져요.</div>';
    } else {
        clip.segments.forEach((seg, i) => {
            const row = document.createElement('div');
            row.className = 'cap-row';
            row.innerHTML = `
                <input type="text" value="${fmtTime(seg.start)}" data-k="start" class="cap-time" title="시작 mm:ss.s">
                <input type="text" value="${fmtTime(seg.end)}" data-k="end" class="cap-time" title="끝 mm:ss.s">
                <input type="text" value="${escapeAttr(seg.text)}" class="cap-text" data-k="text">
                <button type="button" class="cap-play" title="이 부분 재생">▶</button>
                <button type="button" class="cap-del" title="삭제">×</button>
            `;
            list.appendChild(row);

            row.querySelectorAll('input').forEach(inp => {
                inp.addEventListener('change', () => {
                    const k = inp.dataset.k;
                    let v = inp.value;
                    if (k === 'text') {
                        clip.segments[i].text = v;
                    } else {
                        clip.segments[i][k] = parseTime(v);
                        inp.value = fmtTime(clip.segments[i][k]);  // 정규화
                    }
                    saveMeta({ clips: [{ id: clip.id, segments: clip.segments }] });
                });
            });

            row.querySelector('.cap-play').addEventListener('click', () => {
                const v = card.querySelector('video');
                if (!v) return;
                v.currentTime = clip.segments[i].start;
                v.play();
                // end 까지만 재생
                const endT = clip.segments[i].end;
                const stopAt = () => {
                    if (v.currentTime >= endT) { v.pause(); v.removeEventListener('timeupdate', stopAt); }
                };
                v.addEventListener('timeupdate', stopAt);
            });

            row.querySelector('.cap-del').addEventListener('click', () => {
                const removed = clip.segments[i];
                clip.segments.splice(i, 1);
                saveMeta({ clips: [{ id: clip.id, segments: clip.segments }] });
                renderCaptions(card, clip);
                pushUndo(() => {
                    clip.segments.splice(i, 0, removed);
                    saveMeta({ clips: [{ id: clip.id, segments: clip.segments }] });
                    renderCaptions(card, clip);
                });
                showToast(`"${removed.text.slice(0, 20)}" 삭제`, () => {});
            });
        });
    }

    const addBtn = card.querySelector('.add-cap');
    addBtn.onclick = () => {
        clip.segments = clip.segments || [];
        const last = clip.segments[clip.segments.length - 1];
        const start = last ? last.end : 0;
        clip.segments.push({ start, end: start + 2, text: '' });
        saveMeta({ clips: [{ id: clip.id, segments: clip.segments }] });
        renderCaptions(card, clip);
        // 새로 추가된 줄의 텍스트 칸에 포커스
        const rows = card.querySelectorAll('.cap-row');
        const last_row = rows[rows.length - 1];
        if (last_row) last_row.querySelector('.cap-text').focus();
    };
}

function escapeAttr(s) {
    return (s || '').replace(/&/g, '&amp;').replace(/"/g, '&quot;')
        .replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

// ───── 무음 구간 ─────

function renderSilence(card, clip) {
    const list = card.querySelector('.sil-list');
    const hint = card.querySelector('.hint-inline');

    list.innerHTML = '';
    if (!clip.silence_ranges || !clip.silence_ranges.length) {
        list.innerHTML = '<div class="empty-hint">감지된 무음 없음 (분석 전일 수 있음).</div>';
        hint.textContent = '';
        return;
    }

    const skippable = clip.silence_ranges.filter(r => r.suggest_skip);
    const totalSkip = skippable.reduce((s, r) => s + r.duration, 0);
    hint.textContent = `· 스킵 시 ${totalSkip.toFixed(1)}s 단축`;

    clip.silence_ranges.forEach((r, i) => {
        const row = document.createElement('div');
        row.className = 'sil-row';
        row.innerHTML = `
            <input type="checkbox" ${r.suggest_skip ? 'checked' : ''}>
            <span class="sil-range">${fmt(r.start)} — ${fmt(r.end)}</span>
            <span class="sil-dur">${r.duration.toFixed(1)}s</span>
        `;
        row.querySelector('input').addEventListener('change', e => {
            clip.silence_ranges[i].suggest_skip = e.target.checked;
            saveMeta({ clips: [{ id: clip.id, silence_ranges: clip.silence_ranges }] });
            renderSilence(card, clip);
        });
        list.appendChild(row);
    });
}

// ───── 분석 ─────

analyzeBtn.addEventListener('click', async () => {
    analyzeBtn.disabled = true;
    analyzeStatus.className = 'status';
    analyzeStatus.textContent = '분석 시작…';

    try {
        const r = await fetch(`/project/${proj.id}/analyze`, { method: 'POST' });
        const j = await r.json();
        pollJob(j.job_id, analyzeStatus, () => {
            // 끝나면 페이지 새로고침 (간단하게)
            location.reload();
        });
    } catch (err) {
        analyzeStatus.className = 'status err';
        analyzeStatus.textContent = err.message;
        analyzeBtn.disabled = false;
    }
});

// ───── 내보내기 ─────

exportBtn.addEventListener('click', async () => {
    exportBtn.disabled = true;
    exportStatus.className = 'status';
    exportStatus.textContent = '시작…';

    try {
        const r = await fetch(`/project/${proj.id}/export`, { method: 'POST' });
        const j = await r.json();
        pollJob(j.job_id, exportStatus, (final) => {
            if (final.download_url) {
                exportStatus.className = 'status ok';
                exportStatus.innerHTML =
                    `완료 · <a href="${final.download_url}">결과 영상 다운로드</a>`;
            }
            exportBtn.disabled = false;
        });
    } catch (err) {
        exportStatus.className = 'status err';
        exportStatus.textContent = err.message;
        exportBtn.disabled = false;
    }
});

async function pollJob(jobId, statusEl, onDone) {
    const timer = setInterval(async () => {
        const r = await fetch(`/job/${jobId}`);
        const j = await r.json();
        if (j.status === 'done') {
            clearInterval(timer);
            statusEl.innerHTML = j.progress || '완료';
            onDone(j);
        } else if (j.status === 'error') {
            clearInterval(timer);
            statusEl.className = 'status err';
            statusEl.textContent = '오류: ' + (j.error || '알 수 없음');
        } else {
            // "자막 생성 3/5" 처럼 숫자가 있으면 진행바 계산
            const m = (j.progress || '').match(/(\d+)\/(\d+)/);
            if (m) {
                const pct = (parseInt(m[1]) / parseInt(m[2])) * 100;
                statusEl.innerHTML =
                    `${j.progress}<progress value="${pct}" max="100"></progress>`;
            } else {
                statusEl.innerHTML = j.progress || '처리 중…';
            }
        }
    }, 2000);
}

// ───── 초기 렌더 ─────

(proj.clips || []).forEach(renderClip);
