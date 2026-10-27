// Service: todas as chamadas HTTP do front-end aos endpoints de rag_web.py.
// As páginas nunca montam URL nem fazem fetch direto — só chamam Api.*.

async function postJson(url, body) {
  const response = await post(url, body);
  return response.json();
}

async function getJson(url, params) {
  const query = params ? `?${new URLSearchParams(params)}` : '';
  const response = await fetch(url + query);
  return response.json();
}

function post(url, body, init = {}) {
  return fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
    ...init
  });
}


export const Api = {
  listModels: () => getJson('/models'),
  listCollections: () => getJson('/collections'),
  helpGroups: () => getJson('/help-groups'),
  collectionMetadata: collection => getJson('/collection-metadata', { collection }),
  ragQuery: params => getJson('/rag-query', params),

  // Resposta em streaming (SSE): devolve o Response cru, lido por readSseStream.
  ask: (body, signal) => post('/ask', body, { signal }),
  cancelAsk: requestId => post('/ask/cancel', { request_id: requestId }).catch(() => {}),
  forgetTurn: (conversationId, question) =>
    post('/ask/forget-turn', { conversation_id: conversationId, question }).catch(() => {}),

  // Envio de PDFs: XHR em vez de fetch, porque só ele informa o progresso
  // do upload (onProgress recebe de 0 a 1).
  uploadPdfs: (files, onProgress) => new Promise((resolve, reject) => {
    const form = new FormData();
    files.forEach(file => form.append('files', file));
    const xhr = new XMLHttpRequest();
    xhr.open('POST', '/upload');
    xhr.responseType = 'json';
    xhr.upload.onprogress = e => { if (e.lengthComputable) onProgress?.(e.loaded / e.total); };
    xhr.onload = () => resolve(xhr.response || { ok: false, error: `Falha no envio (HTTP ${xhr.status}).` });
    xhr.onerror = () => reject(new Error('Falha na conexão com o servidor.'));
    xhr.send(form);
  }),
  uploadJobs: () => getJson('/upload/jobs'),
  documents: () => getJson('/documents'),
  deleteDocument: source => postJson('/documents/delete', { source })
};
