// QuickCut — 업로드·프로젝트 생성

const form = document.getElementById('newForm');
const input = document.getElementById('videoInput');
const dropzone = document.getElementById('dropzone');
const fileList = document.getElementById('fileList');
const createBtn = document.getElementById('createBtn');
const createStatus = document.getElementById('createStatus');

let files = [];

function refreshList() {
    fileList.innerHTML = files.map(f =>
        `<li>${f.name} · ${(f.size / 1024 / 1024).toFixed(1)} MB</li>`
    ).join('');
    createBtn.disabled = files.length === 0;
}

dropzone.addEventListener('click', e => {
    if (e.target.tagName !== 'INPUT') input.click();
});

input.addEventListener('change', e => {
    files = Array.from(e.target.files);
    refreshList();
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
    const dropped = Array.from(e.dataTransfer.files).filter(f =>
        f.type.startsWith('video/')
    );
    files = files.concat(dropped);
    refreshList();
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
