// Página: consulta bruta às bases RAG, sem LLM (static/rag_view.html).
import { Api } from '../services/api.js';
import { csvQuote, downloadBlob } from '../services/download.js';
import { escapeHtml, textWithImagesHtml } from '../services/text.js';
import { typesetMath } from '../components/answer.js';
import { renderKeywords } from '../components/keywords.js';
import { appendOptions, loadSelectOptions, setSelectMessage } from '../components/select.js';

const byId = id => document.getElementById(id);

const els = {
  question: byId('question'),
  keywords: byId('keywords'),
  collection: byId('collection'),
  filterKey: byId('filter_key'),
  filterValue: byId('filter_value'),
  nResults: byId('n_results'),
  runBtn: byId('btnRun'),
  keywordsLine: byId('keywords-line'),
  status: byId('status'),
  csvLink: byId('download-csv'),
  table: byId('results-table')
};

const IMAGE_STYLE = 'height:480px;max-width:100%;object-fit:contain;';

let metadataFields = {};
let currentRows = [];

async function loadMetadataFields() {
  const collection = els.collection.value;
  metadataFields = {};
  setSelectMessage(els.filterKey, '- todos -');
  setSelectMessage(els.filterValue, '- todos -');
  els.filterValue.disabled = true;
  if (!collection) return;

  try {
    const data = await Api.collectionMetadata(collection);
    metadataFields = data.fields || {};
    // "source" pré-selecionado: é o metadado mais útil pra filtrar (nome do
    // PDF/arquivo-fonte) nas collections que o têm; se a collection não tiver
    // esse campo, o <select> cai no primeiro item normalmente.
    appendOptions(els.filterKey, Object.keys(metadataFields).sort(), 'source');
    onFilterKeyChange();
  } catch (e) {
    // filtro por metadado é opcional: falha aqui não deve travar a consulta
  }
}

function onFilterKeyChange() {
  const values = metadataFields[els.filterKey.value] || [];
  setSelectMessage(els.filterValue, '- todos -');
  appendOptions(els.filterValue, values);
  els.filterValue.disabled = values.length === 0;
}

// Trava/destrava os campos durante a consulta; o combo de valor só volta
// habilitado se o metadado escolhido tiver valores.
function setQuerying(querying) {
  [els.question, els.collection, els.nResults, els.filterKey, els.filterValue, els.keywords]
    .forEach(el => { el.disabled = querying; });
  if (!querying) els.filterValue.disabled = !(metadataFields[els.filterKey.value] || []).length;
}

async function runQuery() {
  const { status, table, csvLink, runBtn } = els;
  const tbody = table.querySelector('tbody');
  const question = els.question.value.trim();

  runBtn.disabled = true;
  tbody.innerHTML = '';
  status.textContent = 'Consultando…';
  table.style.display = 'none';
  csvLink.style.display = 'none';
  if (!question) {
    status.textContent = 'Informe uma pergunta.';
    runBtn.disabled = false;
    return;
  }

  const params = {
    question,
    collection: els.collection.value,
    n_results: els.nResults.value,
    keywords: els.keywords.checked
  };
  const filterKey = els.filterKey.value;
  const filterValue = els.filterValue.value;
  if (filterKey && filterValue) Object.assign(params, { filter_key: filterKey, filter_value: filterValue });

  setQuerying(true);
  renderKeywords(els.keywordsLine, []);
  const data = await Api.ragQuery(params);
  runBtn.disabled = false;
  setQuerying(false);

  if (data.error) {
    status.textContent = data.error;
    return;
  }

  renderKeywords(els.keywordsLine, data.keywords);

  if (data.rows.length === 0) {
    status.textContent = 'Concluído. Nenhum resultado encontrado!';
    return;
  }

  status.textContent = `Concluído. ${data.rows.length} resultados encontrados.`;
  tbody.innerHTML = data.rows.map(row => `
        <tr>
          <td>${row.id}</td>
          <td>${escapeHtml(row.source)}</td>
          <td>${row.page ?? ''}</td>
          <td>${row.distance ?? ''}</td>
          <td>${(row.keywords || []).map(escapeHtml).join(', ')}</td>
          <td><pre>${textWithImagesHtml(row.text, IMAGE_STYLE)}</pre></td>
        </tr>
      `).join('');
  table.style.display = '';
  typesetMath(tbody);
  currentRows = data.rows;
  csvLink.style.display = 'block';
}

function downloadCsv(event) {
  event.preventDefault();
  if (!currentRows.length) return;
  const header = ['id', 'header', 'source', 'page', 'distance', 'keywords', 'text'];
  const csv = [header.join(',')].concat(currentRows.map(row => [
    row.id,
    csvQuote(row.header || ''),
    csvQuote(row.source || ''),
    csvQuote(row.page || ''),
    row.distance ?? '',
    csvQuote((row.keywords || []).join('; ')),
    csvQuote(row.text || '')
  ].join(','))).join('\n');
  downloadBlob(csv, 'text/csv;charset=utf-8;', 'rag-results.csv');
}

els.runBtn.addEventListener('click', runQuery);
els.csvLink.addEventListener('click', downloadCsv);
els.collection.addEventListener('change', loadMetadataFields);
els.filterKey.addEventListener('change', onFilterKeyChange);
els.csvLink.style.display = 'none';
els.question.addEventListener('keypress', event => {
  if (event.key === 'Enter') runQuery();
});

(async function init() {
  await loadSelectOptions(els.collection, Api.listCollections, { selected: 'rag_documentos', errorText: 'erro' });
  await loadMetadataFields();
})();
