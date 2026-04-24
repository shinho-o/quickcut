// QuickCut — 업로드·프로젝트 생성

const form = document.getElementById('newForm');
const input = document.getElementById('videoInput');
const dropzone = document.getElementById('dropzone');
const fileList = document.getElementById('fileList');
const createBtn = document.getElementById('createBtn');
const createStatus = document.getElementById('createStatus');

let files = [];

function refreshList() {
    fileList.innerHTML = files.map((f, i) =>
        `<li>${f.name} · ${(f.size / 1024 / 1024).toFixed(1)} MB
         <button type="button" data-idx="${i}" class="rm" title="빼기">×</button></li>`
    ).join('');
    createBtn.disabled = files.length === 0;
    createBtn.textContent = files.length > 0
        ? `${files.length}개 영상으로 프로젝트 만들기`
        : '프로젝트 만들기';
    fileList.querySelectorAll('.rm').forEach(btn => {
        btn.addEventListener('click', e => {
            e.stopPropagation();
            e.preventDefault();
            files.splice(parseInt(btn.dataset.idx), 1);
            refreshList();
        });
    });
}

function addFiles(incoming) {
    const videos = Array.from(incoming).filter(f => f.type.startsWith('video/'));
    for (const f of videos) {
        const dup = files.some(x =>
            x.name === f.name && x.size === f.size && x.lastModified === f.lastModified);
        if (!dup) files.push(f);
    }
    refreshList();
}

dropzone.addEventListener('click', e => {
    // 삭제 버튼 / 파일 자체 클릭은 입력창 열지 않음
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'BUTTON') return;
    input.click();
});

input.addEventListener('change', e => {
    addFiles(e.target.files);
    // 같은 파일도 다시 선택 가능하도록 값 초기화
    e.target.value = '';
});

['dragenter', 'dragover'].forEach(ev =>
    dropzone.addEventListener(ev, e => {
        e.preventDefault();
        dropzone.classList.add('drag');
    })
);
['dragleave', 'drop'].forEach(ev =>
    dropzone.addEventListener(ev, e => {
        e.preventDefault();
        dropzone.classList.remove('drag');
    })
);
dropzone.addEventListener('drop', e => {
    addFiles(e.dataTransfer.files);
});

form.addEventListener('submit', async e => {
    e.preventDefault();
    if (!files.length) return;

    createBtn.disabled = true;
    createStatus.className = 'status';
    createStatus.textContent = '업로드 중…';

    const fd = new FormData();
    const titleInput = form.querySelector('input[name=title]');
    if (titleInput.value) fd.append('title', titleInput.value);
    files.forEach(f => fd.append('videos', f));

    try {
        const r = await fetch('/project/new', { method: 'POST', body: fd });
        const j = await r.json();
        if (!r.ok) throw new Error(j.error || '업로드 실패');
        location.href = j.redirect;
    } catch (err) {
        createStatus.className = 'status err';
        createStatus.textContent = err.message;
        createBtn.disabled = false;
    }
});

document.querySelectorAll('[data-delete]').forEach(btn => {
    btn.addEventListener('click', async e => {
        e.preventDefault();
        if (!confirm('이 프로젝트를 삭제할까요?')) return;
        const id = btn.dataset.delete;
        await fetch(`/project/${id}/delete`, { method: 'POST' });
        btn.closest('li').remove();
    });
});
