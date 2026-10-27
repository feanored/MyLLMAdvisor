// Página: Enviar PDFs (templates/upload.html). Escolhe/arrasta PDFs, envia
// (com progresso do upload) e acompanha a indexação de cada um por polling
// em /upload/jobs (ver IndexingService no backend).
import { Api } from '../services/api.js';
import { escapeHtml } from '../services/text.js';
import { confirmDelete } from '../components/confirm-dialog.js';

const byId = id => document.getElementById(id);
// Pra valores dentro de atributos: escapeHtml não escapa aspas, e o nome
// do arquivo vem do computador de quem envia.
const attr = value => escapeHtml(value).replace(/"/g, '&quot;');

const els = {
  dropzone: byId('dropzone'),
  fileInput: byId('file-input'),
  selectedList: byId('selected-list'),
  actions: byId('upload-actions'),
  sendBtn: byId('send-btn'),
  clearBtn: byId('clear-btn'),
  progress: byId('upload-progress'),
  progressFill: byId('upload-progress-fill'),
  progressText: byId('upload-progress-text'),
  status: byId('upload-status'),
  jobsList: byId('jobs-list'),
  documentsList: byId('documents-list'),
  documentsCount: byId('documents-count')
};

const POLL_MS = 1500;
const STATUS_LABELS = {
  queued: 'Na fila',
  running: 'Indexando',
  done: 'Indexado',
  skipped: 'Já estava na base',
  error: 'Erro'
};
const PENDING = new Set(['queued', 'running']);

// Arquivos escolhidos e ainda não enviados, por nome (escolher de novo o
// mesmo arquivo não duplica a linha).
const selected = new Map();
let uploading = false;
let pollTimer = null;
let lastStatuses = new Map();  // job id -> status, pra saber quando um termina
let freshDocuments = new Set();
// Algum PDF na fila ou indexando: a exclusão de documentos fica bloqueada
// até terminar (o servidor também recusa, ver IndexingService.remove_document).
let indexing = false;
let documentNames = [];

// --- Arquivos escolhidos ---------------------------------------------------------

function formatSize(bytes) {
  return bytes >= 1024 * 1024 ? `${(bytes / 1024 / 1024).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

function setStatus(text, isError = false) {
  els.status.textContent = text;
  els.status.classList.toggle('error', isError);
}

function addFiles(fileList) {
  const ignored = [];
  for (const file of fileList) {
    if (!file.name.toLowerCase().endsWith('.pdf')) {
      ignored.push(file.name);
      continue;
    }
    selected.set(file.name, file);
  }
  setStatus(ignored.length ? `Ignorado(s), não são PDF: ${ignored.join(', ')}` : '', ignored.length > 0);
  renderSelected();
}

function renderSelected() {
  const files = [...selected.values()];
  els.selectedList.hidden = !files.length;
  els.actions.hidden = !files.length && !uploading;
  els.selectedList.innerHTML = files.map(file => `
    <li>
      <span class="selected-name" title="${attr(file.name)}">${escapeHtml(file.name)}</span>
      <span class="selected-size">${formatSize(file.size)}</span>
      <button type="button" class="remove-btn" data-name="${attr(file.name)}"
              title="Tirar da lista" aria-label="Tirar ${attr(file.name)} da lista"${uploading ? ' disabled' : ''}>×</button>
    </li>`).join('');
  els.sendBtn.textContent = files.length > 1 ? `Enviar e indexar ${files.length} PDFs` : 'Enviar e indexar';
  els.sendBtn.disabled = uploading || !files.length;
  els.clearBtn.disabled = uploading;
}

// --- Envio ------------------------------------------------------------------------

function showProgress(fraction) {
  const pct = Math.round(fraction * 100);
  els.progress.hidden = false;
  els.progressFill.style.width = `${pct}%`;
  els.progressText.textContent = pct < 100 ? `Enviando… ${pct}%` : 'Recebido pelo servidor';
}

async function send() {
  const files = [...selected.values()];
  if (!files.length || uploading) return;
  uploading = true;
  renderSelected();
  renderDocuments();
  setStatus('');
  showProgress(0);

  try {
    const data = await Api.uploadPdfs(files, showProgress);
    if (!data.ok) {
      setStatus(data.error || 'Falha no envio.', true);
      return;
    }
    selected.clear();
    const rejected = (data.rejected || []).map(r => `${r.file}: ${r.reason}`);
    const queued = (data.jobs || []).length;
    const lines = [];
    if (queued) lines.push(`${queued} PDF(s) na fila de indexação.`);
    if (rejected.length) lines.push(`Não enviado(s):\n${rejected.join('\n')}`);
    setStatus(lines.join('\n'), rejected.length > 0);
    await refreshJobs();
  } catch (err) {
    setStatus(err.message || 'Falha na conexão com o servidor.', true);
  } finally {
    uploading = false;
    els.progress.hidden = true;
    renderSelected();
    renderDocuments();
  }
}

// --- Fila de indexação ---------------------------------------------------------------

function formatElapsed(seconds) {
  if (seconds == null) return '';
  const total = Math.round(seconds);
  const m = Math.floor(total / 60);
  const s = String(total % 60).padStart(2, '0');
  return m ? `${m}min ${s}s` : `${total}s`;
}

function renderJobs(jobs) {
  if (!jobs.length) return;  // mantém o texto de "nenhum envio" do HTML
  els.jobsList.innerHTML = jobs.map(job => `
    <li class="job" data-status="${job.status}">
      <span class="job-name" title="${attr(job.file)}">${escapeHtml(job.file)}</span>
      <span class="job-state">${STATUS_LABELS[job.status] || job.status}
        <span class="job-time">${formatElapsed(job.elapsed)}</span></span>
      <span class="job-message">${escapeHtml(job.message || '')}</span>
    </li>`).join('');
}

async function refreshJobs() {
  clearTimeout(pollTimer);
  let jobs = [];
  try {
    jobs = await Api.uploadJobs();
  } catch (e) {
    pollTimer = setTimeout(refreshJobs, POLL_MS * 2);  // servidor fora do ar: tenta de novo
    return;
  }
  renderJobs(jobs);

  // Um PDF acabou de entrar na base: atualiza a lista do menu lateral.
  const finished = jobs.filter(job => job.status === 'done' && PENDING.has(lastStatuses.get(job.id)));
  if (finished.length) {
    finished.forEach(job => freshDocuments.add(job.file));
    loadDocuments();
  }
  lastStatuses = new Map(jobs.map(job => [job.id, job.status]));
  const nowIndexing = jobs.some(job => PENDING.has(job.status));
  if (nowIndexing !== indexing) {
    indexing = nowIndexing;
    renderDocuments();
  }

  if (indexing) pollTimer = setTimeout(refreshJobs, POLL_MS);
}

// --- Documentos na base (menu lateral) --------------------------------------------------

const TRASH_ICON = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"
  stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
  <path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13M10 11v6M14 11v6"/></svg>`;

async function loadDocuments() {
  try {
    documentNames = await Api.documents();
  } catch (e) {
    els.documentsList.innerHTML = '<li class="documents-empty">Não foi possível ler a base. O servidor do Chroma está no ar?</li>';
    return;
  }
  renderDocuments();
}

// Enquanto um envio sobe ou uma indexação roda, nenhum documento pode ser excluído.
const deleteLocked = () => indexing || uploading;

function renderDocuments() {
  const names = documentNames;
  const locked = deleteLocked();
  const label = name => (locked
    ? 'Indexação em andamento: espere terminar para excluir'
    : `Excluir ${attr(name)} da base`);
  els.documentsCount.textContent = names.length ? `${names.length} PDF(s)` : '';
  els.documentsList.innerHTML = names.length
    ? names.map(name => {
      return `
      <li class="${freshDocuments.has(name) ? 'fresh' : ''}">
        <span class="document-name" title="${attr(name)}">${escapeHtml(name)}</span>
        <button type="button" class="document-delete" data-name="${attr(name)}"
                title="${label(name)}" aria-label="${label(name)}"${locked ? ' disabled' : ''}>${TRASH_ICON}</button>
      </li>`;
    }).join('')
    : '<li class="documents-empty">Nenhum documento indexado ainda. Envie o primeiro PDF ao lado.</li>';
}

// Excluir da base é o caminho pra reindexar um PDF que não mudou: reenviado
// sem excluir, ele é pulado ("Já estava na base").
async function deleteDocument(btn) {
  const name = btn.dataset.name;
  const ok = await confirmDelete({
    title: 'Excluir documento da base?',
    detail: name,
    message: 'Os trechos, figuras e equações dele deixam de ser consultados pelo Orientador. ' +
             'O arquivo PDF não é apagado; para reindexá-lo, envie-o de novo depois.'
  });
  if (!ok) return;
  // Uma indexação pode ter começado com o modal aberto.
  if (deleteLocked()) {
    setStatus('Há uma indexação em andamento: espere terminar para excluir documentos.', true);
    return;
  }
  btn.disabled = true;
  try {
    const data = await Api.deleteDocument(name);
    if (!data.ok) {
      setStatus(data.error || 'Não foi possível excluir o documento.', true);
      btn.disabled = false;
      return;
    }
    freshDocuments.delete(name);
    setStatus(`"${name}" excluído da base (${data.removed} trechos). Envie o PDF de novo para reindexá-lo.`);
    await loadDocuments();
  } catch (e) {
    setStatus('Falha na conexão com o servidor.', true);
    btn.disabled = false;
  }
}

// --- Inicialização ----------------------------------------------------------------------

els.fileInput.addEventListener('change', () => {
  addFiles(els.fileInput.files);
  els.fileInput.value = '';  // permite escolher o mesmo arquivo de novo
});

['dragenter', 'dragover'].forEach(type => els.dropzone.addEventListener(type, e => {
  e.preventDefault();
  els.dropzone.classList.add('dragover');
}));
['dragleave', 'dragend'].forEach(type => els.dropzone.addEventListener(type, e => {
  if (!els.dropzone.contains(e.relatedTarget)) els.dropzone.classList.remove('dragover');
}));
els.dropzone.addEventListener('drop', e => {
  e.preventDefault();
  els.dropzone.classList.remove('dragover');
  if (!uploading) addFiles(e.dataTransfer.files);
});
// Um PDF solto fora da área não deve abrir no navegador, saindo da página.
window.addEventListener('dragover', e => e.preventDefault());
window.addEventListener('drop', e => e.preventDefault());

els.selectedList.addEventListener('click', e => {
  const btn = e.target.closest('.remove-btn');
  if (!btn) return;
  selected.delete(btn.dataset.name);
  renderSelected();
});
els.documentsList.addEventListener('click', e => {
  const btn = e.target.closest('.document-delete');
  if (btn && !btn.disabled) deleteDocument(btn);
});
els.sendBtn.addEventListener('click', send);
els.clearBtn.addEventListener('click', () => {
  selected.clear();
  setStatus('');
  renderSelected();
});

loadDocuments();
refreshJobs();
