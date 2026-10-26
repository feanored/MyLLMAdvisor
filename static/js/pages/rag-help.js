// Página: Orientador (templates/orientador.html) — chat simplificado,
// focado na collection padrão (rag_documentos), com help_mode: true (ver
// HELP_PROMPT em services/chat_service.py). Layout de chat: cada pergunta
// vira um "turno" empilhado na thread, `conversationId` dá memória à
// conversa (ver ConversationService no backend).
import { Api } from '../services/api.js';
import { readSseStream } from '../services/sse.js';
import { Timers } from '../services/timer.js';
import { escapeHtml, parseJson } from '../services/text.js';
import { decode, renderAnswerBox, restoreMath } from '../components/answer.js';
import { downloadAnswer } from '../components/answer-export.js';
import { renderKeywords } from '../components/keywords.js';
import { appendOptions } from '../components/select.js';
import { MultiSelect } from '../components/multiselect.js';

const HELP_COLLECTION = 'rag_documentos';
const FILTER_KEY = 'source';

const byId = id => document.getElementById(id);

const els = {
  thread: byId('thread'),
  threadHint: byId('thread-hint'),
  question: byId('question'),
  askBtn: byId('btn'),
  cancelBtn: byId('cancel-btn'),
  status: byId('status'),
  modelDialog: byId('model-dialog'),
  modelSelect: byId('model-select'),
  modelConfirmBtn: byId('model-confirm-btn'),
  modelCancelBtn: byId('model-cancel-btn'),
  newConversationBtn: byId('new-conversation-btn'),
  exportBtn: byId('export-conversations-btn'),
  importBtn: byId('import-conversations-btn'),
  importInput: byId('import-conversations-input'),
  conversationsList: byId('conversations-list')
};

const THREAD_HINT_HTML = `<p class="thread-hint" id="thread-hint">
    Faça uma pergunta sobre os artigos e livros da base pra começar: o chat
    lembra da conversa, então pode ir aprofundando em sequência.
  </p>`;

const globalTimers = new Timers({ total: 'total-timer' });

function makeRequestId() {
  if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
  return `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

let conversationId = makeRequestId();

// --- Conversas salvas (localStorage) --------------------------------------
// Cada conversa é salva como o próprio HTML já renderizado da thread, não
// como dados brutos: reabrir é só reinjetar esse HTML de volta. O histórico
// real (ConversationService) mora em memória do processo Flask: some num
// restart do servidor, mesmo com os turnos antigos ainda visíveis aqui.
const STORAGE_INDEX_KEY = 'myllmadvisor_conversations';
const STORAGE_CONV_PREFIX = 'myllmadvisor_conv_';
const TITLE_MAX_LEN = 60;

// Título = a primeira pergunta da conversa, truncada (igual todo chat faz).
function truncateTitle(text) {
  const clean = text.trim().replace(/\s+/g, ' ');
  return clean.length > TITLE_MAX_LEN ? clean.slice(0, TITLE_MAX_LEN).trim() + '…' : clean;
}

function loadIndex() {
  try {
    const list = JSON.parse(localStorage.getItem(STORAGE_INDEX_KEY) || '[]');
    return Array.isArray(list) ? list : [];
  } catch (e) {
    return [];
  }
}

function saveIndex(list) {
  try {
    localStorage.setItem(STORAGE_INDEX_KEY, JSON.stringify(list));
  } catch (e) { /* storage indisponível/cheio: a conversa atual segue funcionando */ }
}

function loadConversation(id) {
  try {
    const raw = localStorage.getItem(STORAGE_CONV_PREFIX + id);
    return raw ? JSON.parse(raw) : null;
  } catch (e) {
    return null;
  }
}

// --- Importar/exportar conversas (CSV) ------------------------------------
// Uma linha por turno: conversation_id/title repetem por turno da mesma
// conversa; turn_index reconstrói a ordem no import.
const CSV_COLUMNS = ['conversation_id', 'title', 'turn_index', 'turn_id', 'html'];

function csvEscape(value) {
  const str = String(value ?? '');
  return /[",\r\n]/.test(str) ? '"' + str.replace(/"/g, '""') + '"' : str;
}

function csvRow(fields) {
  return fields.map(csvEscape).join(',') + '\r\n';
}

function parseCsv(text) {
  const rows = [];
  let row = [];
  let field = '';
  let inQuotes = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (inQuotes) {
      if (c === '"' && text[i + 1] === '"') { field += '"'; i++; }
      else if (c === '"') { inQuotes = false; }
      else { field += c; }
    } else if (c === '"') {
      inQuotes = true;
    } else if (c === ',') {
      row.push(field);
      field = '';
    } else if (c === '\r') {
      // ignorado: sempre acompanhado de \n (ver csvRow)
    } else if (c === '\n') {
      row.push(field);
      rows.push(row);
      row = [];
      field = '';
    } else {
      field += c;
    }
  }
  if (field !== '' || row.length) {
    row.push(field);
    rows.push(row);
  }
  return rows;
}

function exportConversations() {
  const list = loadIndex();
  if (!list.length) return;
  let csv = csvRow(CSV_COLUMNS);
  for (const { id, title } of list) {
    const stored = loadConversation(id);
    if (!stored || !Array.isArray(stored.turns)) continue;
    stored.turns.forEach((turn, index) => {
      csv += csvRow([id, title, index, turn.id, turn.html]);
    });
  }
  const blob = new Blob(['﻿', csv], { type: 'text/csv;charset=utf-8;' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `myllmadvisor_conversas_${new Date().toISOString().slice(0, 10)}.csv`;
  a.click();
  URL.revokeObjectURL(url);
}

function importConversationsFromCsv(text) {
  const rows = parseCsv(text.replace(/^﻿/, ''));
  if (!rows.length) return;
  const colIndex = CSV_COLUMNS.map(name => rows[0].indexOf(name));
  if (colIndex.includes(-1)) {
    els.status.textContent = 'Arquivo CSV inválido: colunas esperadas não encontradas.';
    return;
  }
  const [idxConv, idxTitle, idxOrder, idxTurn, idxHtml] = colIndex;

  const byConversation = new Map();
  for (const row of rows.slice(1)) {
    const id = row[idxConv];
    if (!id) continue;
    if (!byConversation.has(id)) byConversation.set(id, { title: row[idxTitle] || '', turns: [] });
    byConversation.get(id).turns.push({ order: Number(row[idxOrder]) || 0, id: row[idxTurn], html: row[idxHtml] || '' });
  }

  const importedIds = [];
  for (const [id, conv] of byConversation) {
    conv.turns.sort((a, b) => a.order - b.order);
    const turns = conv.turns.map(({ id: turnId, html }) => ({ id: turnId, html }));
    try {
      localStorage.setItem(STORAGE_CONV_PREFIX + id, JSON.stringify({ turns }));
      importedIds.push(id);
    } catch (e) { /* storage cheio/indisponível: pula esta conversa */ }
  }

  const importedList = importedIds.map(id => ({ id, title: byConversation.get(id).title }));
  const existingList = loadIndex().filter(c => !importedIds.includes(c.id));
  saveIndex([...importedList, ...existingList]);
  renderConversationsList();
  els.status.textContent = `${importedIds.length} conversa(s) importada(s).`;
}

// Turnos apagados pelo "✕" (ver wireDeleteButton): se a resposta ainda
// estiver chegando, persistTurn é chamado de novo ao terminar — sem isso o
// turno reapareceria sozinho no armazenamento.
const deletedTurnIds = new Set();

// Grava o outerHTML de UM turno (não a thread inteira) sob `id`, por
// turnId (upsert) — não por `conversationId`, que pode já ter mudado se o
// usuário trocar de conversa enquanto esta resposta ainda chega.
function persistTurn(id, question, turn) {
  if (deletedTurnIds.has(turn.turnId)) return;
  const list = loadIndex();
  const existing = list.find(c => c.id === id);
  const title = existing ? existing.title : truncateTitle(question);
  const stored = loadConversation(id) || { turns: [] };
  const idx = stored.turns.findIndex(t => t.id === turn.turnId);
  const entry = { id: turn.turnId, html: turn.root.outerHTML };
  if (idx === -1) stored.turns.push(entry);
  else stored.turns[idx] = entry;
  try {
    localStorage.setItem(STORAGE_CONV_PREFIX + id, JSON.stringify(stored));
  } catch (e) {
    return;  // storage cheio/indisponível: a conversa atual não é afetada
  }
  saveIndex([{ id, title }, ...list.filter(c => c.id !== id)]);
  renderConversationsList();
}

// Remove UM turno (não a conversa inteira, ver deleteConversation).
function removeTurn(id, turnId) {
  const stored = loadConversation(id);
  if (!stored || !Array.isArray(stored.turns)) return;
  stored.turns = stored.turns.filter(t => t.id !== turnId);
  try {
    localStorage.setItem(STORAGE_CONV_PREFIX + id, JSON.stringify(stored));
  } catch (e) { /* storage indisponível: a tela já foi atualizada */ }
}

function deleteConversation(id) {
  // A conversa ATIVA não pode ser excluída enquanto uma resposta está
  // sendo renderizada nela (ver setAsking).
  if (current.abortController && id === conversationId) return;
  try {
    localStorage.removeItem(STORAGE_CONV_PREFIX + id);
  } catch (e) { /* ignore */ }
  saveIndex(loadIndex().filter(c => c.id !== id));
  if (id === conversationId) startNewConversation();
  else renderConversationsList();
}

function renderConversationsList() {
  const list = loadIndex();
  els.exportBtn.disabled = current.abortController ? true : !list.length;
  if (!list.length) {
    els.conversationsList.innerHTML = '<span class="conversations-empty">Nenhuma conversa salva ainda.</span>';
    return;
  }
  els.conversationsList.innerHTML = list.map(c => `
    <div class="conversation-item${c.id === conversationId ? ' active' : ''}" data-id="${c.id}">
      <span class="conversation-title" title="${escapeHtml(c.title)}">${escapeHtml(c.title)}</span>
      <button type="button" class="conversation-delete" data-id="${c.id}"
              title="Excluir conversa" aria-label="Excluir conversa">×</button>
    </div>
  `).join('');
}

function startNewConversation() {
  // Bloqueado enquanto uma resposta está sendo renderizada (ver setAsking).
  if (current.abortController) return;
  conversationId = makeRequestId();
  byId('thread-inner').innerHTML = THREAD_HINT_HTML;
  els.threadHint = byId('thread-hint');
  renderConversationsList();
}

function switchConversation(id) {
  if (id === conversationId) return;
  // Bloqueado enquanto uma resposta está sendo renderizada (ver setAsking).
  if (current.abortController) return;
  const stored = loadConversation(id);
  if (!stored || !Array.isArray(stored.turns)) return;  // entrada órfã (ex.: storage alterado por fora)
  conversationId = id;
  els.threadHint = null;
  const threadInner = byId('thread-inner');
  threadInner.innerHTML = stored.turns.map(t => t.html).join('');
  wireRestoredTurns();
  restoreMath(threadInner);
  scrollThreadToBottom();
  renderConversationsList();
}

// Documento (chave) -> {label, sources, default_checked}, de /help-groups —
// decide o filtro "source" enviado ao /ask.
let groups = {};
const docsSelect = new MultiSelect({
  wrap: byId('docs_wrap'), button: byId('docs_btn'), panel: byId('docs_panel')
});
const current = { abortController: null, requestId: null };

// Mostrado no answer-box assim que o turno é criado; o handler "content"
// (ver ask()) o substitui sozinho ao sobrescrever o innerHTML com o primeiro
// pedaço da resposta, então não precisa de lógica própria pra sumir.
const LOADING_INDICATOR_HTML = '<div class="loading-indicator"><span></span><span></span><span></span></div>';

// --- Modelo (popup via Ctrl+clique em "Perguntar") ------------------------------

// null = usa o default do controller; qualquer outra coisa sobrescreve
// `model` no payload do /ask. Vale só enquanto a página estiver aberta.
let selectedModel = null;
let modelsLoaded = false;

function updateAskBtnTitle() {
  els.askBtn.title = selectedModel
    ? `Perguntar (modelo: ${selectedModel}), Ctrl+clique pra trocar`
    : 'Perguntar, Ctrl+clique pra escolher outro modelo';
}

async function ensureModelsLoaded() {
  if (modelsLoaded) return;
  els.modelSelect.innerHTML = '<option value="">Carregando…</option>';
  try {
    const models = await Api.listModels();
    els.modelSelect.innerHTML = '';
    const defaultOpt = document.createElement('option');
    defaultOpt.value = '';
    defaultOpt.textContent = 'Padrão do sistema';
    els.modelSelect.appendChild(defaultOpt);
    appendOptions(els.modelSelect, models);
    modelsLoaded = true;
  } catch (e) {
    els.modelSelect.innerHTML = '<option value="">Erro ao carregar modelos</option>';
  }
}

async function openModelDialog() {
  await ensureModelsLoaded();
  els.modelSelect.value = selectedModel || '';
  els.modelDialog.showModal();
}

// --- Combo de documentos ---------------------------------------------------

async function loadGroups() {
  try {
    groups = await Api.helpGroups();
  } catch (e) {
    groups = {};
  }

  const entries = Object.entries(groups);
  if (!entries.length) {
    docsSelect.reset('Nenhum documento indexado');
    return;
  }

  const defaultKeys = entries.filter(([, group]) => group.default_checked).map(([key]) => key);
  docsSelect.setValues(entries.map(([key, group]) => ({ value: key, label: group.label })), defaultKeys);
}

// Nada marcado usa os grupos com default_checked; sem nenhum default
// (padrão de /help-groups), a busca vale pra collection inteira.
function selectedSources() {
  const defaultKeys = Object.keys(groups).filter(key => groups[key].default_checked);
  const keys = docsSelect.selected.size ? [...docsSelect.selected] : defaultKeys;
  return keys.flatMap(key => groups[key]?.sources || []);
}

// --- Thread (turnos da conversa) ------------------------------------------------

// Monta o bloco de um turno novo (pergunta + resposta) e devolve as
// referências que `ask()` precisa pra preenchê-lo enquanto a resposta chega.
function createTurn(question) {
  // Aleatório, não sequencial: evita colidir com o id de um turno restaurado
  // de uma conversa salva (ver persistTurn).
  const turnId = makeRequestId();
  const timerId = `turn-answer-timer-${turnId}`;

  const root = document.createElement('div');
  root.className = 'turn card';
  root.dataset.turnId = turnId;
  root.innerHTML = `
    <div class="turn-actions">
      <button type="button" class="turn-delete-btn" title="Excluir esta resposta" aria-label="Excluir esta resposta">✕</button>
      <button type="button" class="download-btn" title="Baixar resposta em HTML" aria-label="Baixar resposta em HTML">⬇️</button>
    </div>
    <div class="turn-question-block">
      <div class="section-title"><span>PERGUNTA:</span></div>
      <div class="turn-question-text"></div>
    </div>
    <div class="keywords-line" style="display:none"></div>
    <div class="answer-header">
      <div class="section-title">
        <span>RESPOSTA</span>
        <span id="${timerId}" class="timer">-</span>
      </div>
    </div>
    <div class="answer-box"></div>
  `;
  root.querySelector('.turn-question-text').textContent = question;

  return {
    root,
    turnId,
    answerBox: root.querySelector('.answer-box'),
    keywordsLine: root.querySelector('.keywords-line'),
    timers: new Timers({ answer: timerId })
  };
}

// Lê pergunta/resposta/palavras-chave direto do DOM do turno, então funciona
// igual pra um turno novo e pra um restaurado (ver wireRestoredTurns).
function wireDownloadButton(turnRoot) {
  const downloadBtn = turnRoot.querySelector('.download-btn');
  const answerBox = turnRoot.querySelector('.answer-box');
  if (!downloadBtn || !answerBox) return;
  downloadBtn.addEventListener('click', () => downloadAnswer({
    answerBox,
    question: turnRoot.querySelector('.turn-question-text')?.textContent || '',
    keywords: [...turnRoot.querySelectorAll('.keyword-chip')].map(el => el.textContent),
    model: 'orientador',
    collection: HELP_COLLECTION
  }));
}

// Tira o turno da tela, do armazenamento (ver removeTurn) e avisa o backend
// pra esquecer o par pergunta/resposta (ver ConversationService.forget_turn).
function wireDeleteButton(turnRoot, ownerConversationId) {
  const deleteBtn = turnRoot.querySelector('.turn-delete-btn');
  if (!deleteBtn) return;
  deleteBtn.addEventListener('click', () => {
    const turnId = turnRoot.dataset.turnId;
    const question = turnRoot.querySelector('.turn-question-text')?.textContent || '';
    deletedTurnIds.add(turnId);
    turnRoot.remove();
    removeTurn(ownerConversationId, turnId);
    Api.forgetTurn(ownerConversationId, question);
  });
}

function wireTurnActions(turnRoot, ownerConversationId) {
  wireDownloadButton(turnRoot);
  wireDeleteButton(turnRoot, ownerConversationId);
}

function wireRestoredTurns() {
  byId('thread-inner').querySelectorAll('.turn').forEach(turnEl => wireTurnActions(turnEl, conversationId));
}

function scrollThreadToBottom() {
  els.thread.scrollTop = els.thread.scrollHeight;
}

function appendTurn(turn) {
  if (els.threadHint) {
    els.threadHint.remove();
    els.threadHint = null;
  }
  byId('thread-inner').appendChild(turn.root);
  scrollThreadToBottom();
}

// --- Pergunta --------------------------------------------------------------------

function setAsking(isAsking) {
  els.askBtn.disabled = isAsking;
  els.askBtn.style.display = isAsking ? 'none' : '';
  els.cancelBtn.style.display = isAsking ? '' : 'none';
  // Trava a barra lateral (ver guards em switchConversation/
  // deleteConversation/startNewConversation).
  els.newConversationBtn.disabled = isAsking;
  els.exportBtn.disabled = isAsking || !loadIndex().length;
  els.importBtn.disabled = isAsking;
  els.conversationsList.classList.toggle('busy', isAsking);
}

function cancelAsk() {
  if (current.abortController) current.abortController.abort();
  if (current.requestId) Api.cancelAsk(current.requestId);
}

async function ask() {
  const question = els.question.value.trim();
  if (!question) return;

  // Fixado aqui: este turno continua pertencendo à conversa em que foi
  // feito, mesmo se o usuário trocar de conversa antes da resposta chegar.
  const askConversationId = conversationId;
  // Decidido pelo que já está na tela, não pela memória do backend (ver
  // ChatService.stream_answer).
  const isFirstTurn = !byId('thread-inner').querySelector('.turn');
  const filterValue = selectedSources();

  current.abortController = new AbortController();
  current.requestId = makeRequestId();

  els.question.value = '';
  globalTimers.reset();
  globalTimers.start('total');
  setAsking(true);
  els.status.textContent = 'Processando a consulta...';

  const turn = createTurn(question);
  appendTurn(turn);
  turn.answerBox.innerHTML = LOADING_INDICATOR_HTML;
  wireTurnActions(turn.root, askConversationId);
  // Salva já na primeira pergunta: a conversa aparece na barra lateral desde
  // o início, não só depois de a resposta terminar (igual todo chat faz).
  persistTurn(askConversationId, question, turn);

  let answerBuf = '';
  let turnFigures = [];
  let turnEquations = [];

  const finish = text => {
    globalTimers.stop('total');
    turn.timers.stop('answer');
    setAsking(false);
    els.status.textContent = text;
  };

  // Um handler por evento de /ask (ver ChatService.stream_answer). Sem
  // "think" no pedido, o evento thinking nunca chega.
  const handlers = {
    debug_rows: data => {
      try {
        console.clear();
        console.table(JSON.parse(data));
      } catch (e) {
        console.log('debug_rows (raw):', data);
      }
    },
    keywords: data => renderKeywords(turn.keywordsLine, parseJson(data, [])),
    figures: data => { turnFigures = parseJson(data, []); },
    equations: data => { turnEquations = parseJson(data, []); },
    content: data => {
      if (!turn.timers.running('answer')) turn.timers.start('answer');
      answerBuf += data;
      renderAnswerBox(turn.answerBox, answerBuf, turnFigures, turnEquations);
      els.status.textContent = 'Respondendo..';
      scrollThreadToBottom();
    },
    done: () => {
      finish('Concluído.');
      if (answerBuf.trim()) {
        renderAnswerBox(turn.answerBox, answerBuf, turnFigures, turnEquations);
      }
      scrollThreadToBottom();
      persistTurn(askConversationId, question, turn);
    },
    error_msg: data => {
      globalTimers.stop('total');
      turn.timers.stop('answer');
      turn.answerBox.innerHTML = 'Erro: ' + decode(data, turnFigures);
      setAsking(false);
      els.status.textContent = 'Erro.';
      persistTurn(askConversationId, question, turn);
      return false;
    },
    cancelled: () => {
      finish('Cancelado.');
      if (!answerBuf.trim()) turn.answerBox.innerHTML = '';
      persistTurn(askConversationId, question, turn);
      return false;
    }
  };

  const payload = {
    question, collection: HELP_COLLECTION, context: true, request_id: current.requestId,
    help_mode: true, conversation_id: askConversationId, is_first_turn: isFirstTurn,
    filter_key: FILTER_KEY, filter_value: filterValue
  };
  if (selectedModel) payload.model = selectedModel;

  try {
    const response = await Api.ask(payload, current.abortController.signal);
    if (!response.ok || !response.body) {
      throw new Error('Falha na conexão com o servidor.');
    }
    await readSseStream(response, (name, data) => handlers[name]?.(data));
  } catch (err) {
    globalTimers.stop('total');
    turn.timers.stop('answer');
    if (err.name === 'AbortError') {
      els.status.textContent = 'Cancelado.';
      if (!answerBuf.trim()) turn.answerBox.innerHTML = '';
    } else {
      turn.answerBox.innerHTML = 'Erro: ' + (err.message || 'Falha na conexão.');
      els.status.textContent = 'Erro.';
    }
    setAsking(false);
    persistTurn(askConversationId, question, turn);
  } finally {
    current.abortController = null;
    current.requestId = null;
  }
}

// --- Inicialização -----------------------------------------------------------

els.askBtn.addEventListener('click', e => {
  if (e.ctrlKey || e.metaKey) {
    e.preventDefault();
    openModelDialog();
    return;
  }
  ask();
});
els.cancelBtn.addEventListener('click', cancelAsk);
els.modelConfirmBtn.addEventListener('click', () => {
  selectedModel = els.modelSelect.value || null;
  updateAskBtnTitle();
  els.modelDialog.close();
});
els.modelCancelBtn.addEventListener('click', () => els.modelDialog.close());
els.question.addEventListener('keydown', e => {
  // Enter envia (como num composer de chat); Shift+Enter quebra linha.
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    ask();
  }
});
els.newConversationBtn.addEventListener('click', startNewConversation);
els.exportBtn.addEventListener('click', exportConversations);
els.importBtn.addEventListener('click', () => els.importInput.click());
els.importInput.addEventListener('change', async () => {
  const file = els.importInput.files[0];
  els.importInput.value = '';
  if (file) importConversationsFromCsv(await file.text());
});
// Delegação: a lista inteira é recriada a cada render (ver renderConversationsList).
els.conversationsList.addEventListener('click', e => {
  const delBtn = e.target.closest('.conversation-delete');
  if (delBtn) {
    deleteConversation(delBtn.dataset.id);
    return;
  }
  const item = e.target.closest('.conversation-item');
  if (item) switchConversation(item.dataset.id);
});

updateAskBtnTitle();
loadGroups();
renderConversationsList();
