// Página: Orientador (templates/orientador.html) — chat simplificado,
// focado na base de documentos do usuário (o servidor a escolhe pela conta
// logada; HELP_COLLECTION só dá nome ao HTML baixado), com help_mode: true (ver
// HELP_PROMPT em services/chat_service.py). Layout de chat: cada pergunta
// vira um "turno" empilhado na thread, `conversationId` dá memória à
// conversa, e as conversas ficam guardadas na conta do usuário (ver
// ConversationService no backend).
import { Api } from '../services/api.js';
import { readSseStream } from '../services/sse.js';
import { Timers } from '../services/timer.js';
import { escapeHtml, parseJson } from '../services/text.js';
import { decode, renderAnswerBox } from '../components/answer.js';
import { downloadAnswer } from '../components/answer-export.js';
import { renderKeywords } from '../components/keywords.js';
import { appendOptions } from '../components/select.js';
import { MultiSelect } from '../components/multiselect.js';
import { confirmAction, confirmDelete } from '../components/confirm-dialog.js';

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

// --- Conversas guardadas na conta -----------------------------------------
// Cada turno é gravado no servidor pelo próprio /ask (ver
// ConversationService): a pergunta ao chegar, a resposta ao terminar — mesmo
// que esta página seja fechada no meio. Aqui só se lista, reabre, exclui,
// importa e exporta.
const TITLE_MAX_LEN = 60;  // o mesmo CONVERSATION_TITLE_MAX_LEN de models/config.py

// Barra lateral: [{id, title}], da mais recente para a mais antiga (/conversations).
let conversations = [];
// Conversa sendo carregada do servidor (ver switchConversation).
let openingId = null;

// Título = a primeira pergunta da conversa, truncada (igual todo chat faz).
function truncateTitle(text) {
  const clean = text.trim().replace(/\s+/g, ' ');
  return clean.length > TITLE_MAX_LEN ? clean.slice(0, TITLE_MAX_LEN).trim() + '…' : clean;
}

async function loadConversations() {
  try {
    const list = await Api.listConversations();
    if (Array.isArray(list)) conversations = list;
  } catch (e) { /* servidor fora do ar: fica a lista que já está na tela */ }
  renderConversationsList();
}

// A conversa vai para o topo da barra lateral já na primeira pergunta, sem
// esperar a resposta (igual todo chat faz); loadConversations confirma depois.
function showConversationOnTop(id, question) {
  const entry = conversations.find(c => c.id === id) || { id, title: truncateTitle(question) };
  conversations = [entry, ...conversations.filter(c => c.id !== id)];
  renderConversationsList();
}

// --- Importar/exportar conversas (CSV) ------------------------------------
// Uma linha por turno: conversation_id/title repetem por turno da mesma
// conversa; turn_index reconstrói a ordem no import. As listas do turno
// (keywords, figures, equations) vão em JSON.
const CSV_COLUMNS = ['conversation_id', 'title', 'turn_index', 'turn_id', 'question', 'answer',
                     'status', 'keywords', 'figures', 'equations'];
// O import também aceita o CSV de antes das contas, com o HTML de cada turno
// numa coluna "html" no lugar de question/answer (ver turnFromLegacyHtml).
const CSV_REQUIRED = ['conversation_id', 'title', 'turn_index'];

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

async function exportConversations() {
  let list = null;
  try {
    list = await Api.exportConversations();
  } catch (e) { /* tratado abaixo */ }
  if (!Array.isArray(list)) {
    els.status.textContent = 'Não foi possível exportar as conversas.';
    return;
  }
  if (!list.length) return;
  let csv = csvRow(CSV_COLUMNS);
  for (const { id, title, turns } of list) {
    turns.forEach((turn, index) => {
      csv += csvRow([id, title, index, turn.id, turn.question, turn.answer, turn.status,
                     JSON.stringify(turn.keywords || []), JSON.stringify(turn.figures || []),
                     JSON.stringify(turn.equations || [])]);
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

// Grava na conta as conversas lidas de um CSV (ou do localStorage antigo).
// Devolve true se deu certo.
async function saveImported(list) {
  let result = null;
  try {
    result = await Api.importConversations(list);
  } catch (e) { /* tratado abaixo */ }
  if (!result || !result.ok) {
    els.status.textContent = result?.error || 'Não foi possível importar as conversas.';
    return false;
  }
  els.status.textContent = `${result.imported} conversa(s) importada(s).`;
  await loadConversations();
  return true;
}

async function importConversationsFromCsv(text) {
  const rows = parseCsv(text.replace(/^﻿/, ''));
  if (!rows.length) return;
  const header = rows[0];
  const col = Object.fromEntries([...CSV_COLUMNS, 'html'].map(name => [name, header.indexOf(name)]));
  const legacy = col.html !== -1;
  if (CSV_REQUIRED.some(name => col[name] === -1) || (!legacy && col.question === -1)) {
    els.status.textContent = 'Arquivo CSV inválido: colunas esperadas não encontradas.';
    return;
  }
  const cell = (row, name) => (col[name] === -1 ? undefined : row[col[name]]);

  const byConversation = new Map();
  for (const row of rows.slice(1)) {
    const id = cell(row, 'conversation_id');
    if (!id) continue;
    if (!byConversation.has(id)) byConversation.set(id, { id, title: cell(row, 'title') || '', turns: [] });
    const turn = legacy ? turnFromLegacyHtml(cell(row, 'html')) : {
      question: cell(row, 'question'),
      answer: cell(row, 'answer'),
      status: cell(row, 'status'),
      keywords: parseJson(cell(row, 'keywords'), []),
      figures: parseJson(cell(row, 'figures'), []),
      equations: parseJson(cell(row, 'equations'), [])
    };
    byConversation.get(id).turns.push({ order: Number(cell(row, 'turn_index')) || 0, ...turn });
  }

  const list = [...byConversation.values()];
  list.forEach(conv => conv.turns.sort((a, b) => a.order - b.order));
  await saveImported(list);
}

// --- Conversas de antes das contas -----------------------------------------
// Antes, cada turno era guardado como o HTML já renderizado (no localStorage
// do navegador e no CSV exportado). Para gravá-lo na conta, o HTML volta ao
// texto cru da resposta, o caminho inverso de decode() em answer.js: negrito,
// títulos, tabelas e fórmulas (do data-source dos encaixes) voltam ao
// markdown/LaTeX; as figuras saem do texto e voltam pela menção "Figura N".
const LEGACY_INDEX_KEY = 'myllmadvisor_conversations';
const LEGACY_CONV_PREFIX = 'myllmadvisor_conv_';

function markdownTable(table) {
  const rows = [...table.querySelectorAll('tr')]
    .map(tr => [...tr.children].map(cell => [...cell.childNodes].map(markdownOf).join('').trim()));
  if (!rows.length) return '';
  const line = cells => `| ${cells.join(' | ')} |`;
  return [line(rows[0]), line(rows[0].map(() => '---')), ...rows.slice(1).map(line)].join('\n');
}

function markdownOf(node) {
  if (node.nodeType === Node.TEXT_NODE) return node.textContent;
  if (node.nodeType !== Node.ELEMENT_NODE) return '';
  if (node.classList.contains('math-slot')) return node.dataset.source || node.textContent;
  if (node.dataset.segType === 'figure') return '';
  const inner = () => [...node.childNodes].map(markdownOf).join('');
  switch (node.tagName) {
    case 'BR': return '\n';
    case 'B': case 'STRONG': return `**${inner()}**`;
    case 'I': case 'EM': return `*${inner()}*`;
    case 'H2': return `## ${inner()}\n`;
    case 'H3': return `### ${inner()}\n`;
    case 'HR': return '---';
    case 'PRE': return '```' + node.textContent + '```';
    case 'TABLE': return markdownTable(node);
    case 'IMG': return '';
    default: return inner();
  }
}

function turnFromLegacyHtml(html) {
  // <template>: o HTML só é analisado, sem carregar imagens nem rodar nada.
  const tpl = document.createElement('template');
  tpl.innerHTML = html || '';
  const root = tpl.content;
  const box = root.querySelector('.answer-box');
  let answer = box ? markdownOf(box).trim() : '';
  let status = 'done';
  if (answer.startsWith('Erro: ')) {
    status = 'error';
    answer = answer.slice('Erro: '.length);
  }
  return {
    question: root.querySelector('.turn-question-text')?.textContent || '',
    answer,
    status,
    keywords: [...root.querySelectorAll('.keyword-chip')].map(el => el.textContent),
    figures: box ? [...box.querySelectorAll('[data-seg-type="figure"]')]
      .map(el => ({ path: el.dataset.figKey, caption: el.querySelector('.answer-figure-caption')?.textContent || '' }))
      .filter(fig => fig.path) : [],
    equations: []
  };
}

function legacyIndex() {
  try {
    const list = JSON.parse(localStorage.getItem(LEGACY_INDEX_KEY) || '[]');
    return Array.isArray(list) ? list : [];
  } catch (e) {
    return [];
  }
}

function legacyConversations() {
  return legacyIndex().map(({ id, title }) => {
    let stored = null;
    try {
      stored = JSON.parse(localStorage.getItem(LEGACY_CONV_PREFIX + id) || 'null');
    } catch (e) { /* entrada corrompida: fica de fora */ }
    const turns = Array.isArray(stored?.turns) ? stored.turns.map(t => turnFromLegacyHtml(t.html)) : [];
    return { id, title, turns };
  }).filter(conv => conv.turns.length);
}

function clearLegacyStorage() {
  try {
    legacyIndex().forEach(({ id }) => localStorage.removeItem(LEGACY_CONV_PREFIX + id));
    localStorage.removeItem(LEGACY_INDEX_KEY);
  } catch (e) { /* storage indisponível */ }
}

// Conversas que ficaram no localStorage deste navegador: oferece levá-las
// para a conta (e, levadas, apaga-as daqui). Recusou, pergunta de novo na
// próxima vez que a página abrir.
async function offerLegacyImport() {
  const legacy = legacyConversations();
  if (!legacy.length) return;
  const ok = await confirmAction({
    title: 'Importar conversas deste navegador?',
    message: `Há ${legacy.length} conversa(s) salva(s) neste navegador, de antes das contas. ` +
             'Importadas, elas passam a ficar na sua conta, disponíveis em qualquer navegador.',
    confirmLabel: 'Importar'
  });
  if (ok && await saveImported(legacy)) clearLegacyStorage();
}

// --- Barra lateral ------------------------------------------------------------

async function deleteConversation(id) {
  // A conversa ATIVA não pode ser excluída enquanto uma resposta está
  // sendo renderizada nela (ver setAsking).
  if (current.abortController && id === conversationId) return;
  try {
    await Api.deleteConversation(id);
  } catch (e) { /* loadConversations abaixo devolve a conversa à lista se ela não saiu */ }
  conversations = conversations.filter(c => c.id !== id);
  if (id === conversationId) startNewConversation();
  else renderConversationsList();
  loadConversations();
}

async function confirmDeleteConversation(id) {
  if (current.abortController && id === conversationId) return;  // ver deleteConversation
  const entry = conversations.find(c => c.id === id);
  const ok = await confirmDelete({
    title: 'Excluir conversa?',
    detail: entry?.title || '',
    message: 'Todas as perguntas e respostas desta conversa são apagadas da sua conta. Não dá para desfazer.'
  });
  if (ok) deleteConversation(id);
}

function renderConversationsList() {
  els.exportBtn.disabled = current.abortController ? true : !conversations.length;
  if (!conversations.length) {
    els.conversationsList.innerHTML = '<span class="conversations-empty">Nenhuma conversa salva ainda.</span>';
    return;
  }
  els.conversationsList.innerHTML = conversations.map(c => `
    <div class="conversation-item${c.id === conversationId ? ' active' : ''}" data-id="${escapeHtml(c.id)}">
      <span class="conversation-title" title="${escapeHtml(c.title)}">${escapeHtml(c.title)}</span>
      <button type="button" class="conversation-delete" data-id="${escapeHtml(c.id)}"
              title="Excluir conversa" aria-label="Excluir conversa">×</button>
    </div>
  `).join('');
}

function startNewConversation() {
  // Bloqueado enquanto uma resposta está sendo renderizada (ver setAsking).
  if (current.abortController) return;
  openingId = null;
  conversationId = makeRequestId();
  byId('thread-inner').innerHTML = THREAD_HINT_HTML;
  els.threadHint = byId('thread-hint');
  renderConversationsList();
}

async function switchConversation(id) {
  if (id === conversationId) return;
  // Bloqueado enquanto uma resposta está sendo renderizada (ver setAsking).
  if (current.abortController) return;
  openingId = id;
  els.status.textContent = 'Abrindo a conversa…';
  let conversation = null;
  try {
    conversation = await Api.getConversation(id);
  } catch (e) { /* tratado abaixo */ }
  // Outra conversa (ou "Nova conversa") foi escolhida enquanto esta carregava.
  if (openingId !== id || current.abortController) return;
  openingId = null;
  if (!Array.isArray(conversation?.turns)) {
    els.status.textContent = conversation?.error || 'Não foi possível abrir a conversa.';
    loadConversations();
    return;
  }
  els.status.textContent = '';
  conversationId = id;
  const threadInner = byId('thread-inner');
  if (conversation.turns.length) {
    threadInner.replaceChildren(...conversation.turns.map(renderStoredTurn));
    els.threadHint = null;
  } else {
    threadInner.innerHTML = THREAD_HINT_HTML;
    els.threadHint = byId('thread-hint');
  }
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

// Monta o bloco de um turno (pergunta + resposta) e devolve as referências
// que `ask()` precisa pra preenchê-lo enquanto a resposta chega. `turnId`:
// o id gravado no servidor, ao reabrir uma conversa; turno novo ganha um.
function createTurn(question, turnId = makeRequestId()) {
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

// Turno de uma conversa reaberta, renderizado de novo a partir do que o
// servidor guardou (texto cru da resposta + figuras/equações do RAG).
function renderStoredTurn(stored) {
  const turn = createTurn(stored.question, stored.id);
  renderKeywords(turn.keywordsLine, stored.keywords);
  if (stored.status === 'error') {
    turn.answerBox.innerHTML = 'Erro: ' + decode(stored.answer, stored.figures);
  } else {
    renderAnswerBox(turn.answerBox, stored.answer, stored.figures, stored.equations);
  }
  wireTurnActions(turn.root);
  return turn.root;
}

// Lê pergunta/resposta/palavras-chave direto do DOM do turno, então funciona
// igual pra um turno novo e pra um reaberto.
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

// Tira o turno da tela e da conversa guardada, e com isso da memória das
// próximas perguntas (ver ConversationService.delete_turn). Se a resposta
// ainda estiver chegando, o servidor não o grava de volta ao terminar.
function wireDeleteButton(turnRoot) {
  const deleteBtn = turnRoot.querySelector('.turn-delete-btn');
  if (!deleteBtn) return;
  deleteBtn.addEventListener('click', async () => {
    const question = turnRoot.querySelector('.turn-question-text')?.textContent || '';
    const ok = await confirmDelete({
      title: 'Excluir esta resposta?',
      detail: question,
      message: 'A pergunta e a resposta saem da conversa, e o chat deixa de lembrar delas nas próximas perguntas.'
    });
    if (!ok || !turnRoot.isConnected) return;
    turnRoot.remove();
    Api.forgetTurn(turnRoot.dataset.turnId);
  });
}

function wireTurnActions(turnRoot) {
  wireDownloadButton(turnRoot);
  wireDeleteButton(turnRoot);
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
  els.exportBtn.disabled = isAsking || !conversations.length;
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
  wireTurnActions(turn.root);
  showConversationOnTop(askConversationId, question);

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
    },
    error_msg: data => {
      globalTimers.stop('total');
      turn.timers.stop('answer');
      turn.answerBox.innerHTML = 'Erro: ' + decode(data, turnFigures);
      setAsking(false);
      els.status.textContent = 'Erro.';
      return false;
    },
    cancelled: () => {
      finish('Cancelado.');
      if (!answerBuf.trim()) turn.answerBox.innerHTML = '';
      return false;
    }
  };

  const payload = {
    question, collection: HELP_COLLECTION, context: true, request_id: current.requestId,
    help_mode: true, conversation_id: askConversationId, turn_id: turn.turnId, is_first_turn: isFirstTurn,
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
  } finally {
    current.abortController = null;
    current.requestId = null;
    // Título e ordem como o servidor gravou (ou a conversa sai da lista, se
    // nem a pergunta chegou a ser gravada).
    loadConversations();
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
    confirmDeleteConversation(delBtn.dataset.id);
    return;
  }
  const item = e.target.closest('.conversation-item');
  if (item) switchConversation(item.dataset.id);
});

updateAskBtnTitle();
loadGroups();
renderConversationsList();
loadConversations().then(offerLegacyImport);
