// Service: todas as chamadas HTTP do front-end aos endpoints de rag_web.py.
// As páginas nunca montam URL nem fazem fetch direto — só chamam Api.*.

// Login vencido (ou encerrado por uma troca de senha em outro navegador) com
// a tela aberta: o servidor responde 401 e a página recarrega, abrindo o
// popup de login (ver _load_user em rag_web.py). As rotas /auth/* respondem
// 401 de propósito (senha errada) e ficam de fora.
function checkSession(url, response) {
  if (response.status === 401 && !url.startsWith('/auth/')) window.location.reload();
  return response;
}

async function postJson(url, body) {
  const response = await post(url, body);
  return response.json();
}

async function getJson(url, params) {
  const query = params ? `?${new URLSearchParams(params)}` : '';
  const response = checkSession(url, await fetch(url + query));
  return response.json();
}

async function post(url, body, init = {}) {
  return checkSession(url, await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
    ...init
  }));
}


export const Api = {
  // Contas: todas devolvem {ok, error?}.
  login: (email, password) => postJson('/auth/login', { email, password }),
  register: (email, password, inviteCode) =>
    postJson('/auth/register', { email, password, invite_code: inviteCode }),
  logout: () => postJson('/auth/logout'),
  changePassword: (currentPassword, newPassword) =>
    postJson('/auth/password', { current_password: currentPassword, new_password: newPassword }),

  // Conversas do Orientador, guardadas na conta (ver ConversationService).
  listConversations: () => getJson('/conversations'),
  getConversation: id => getJson(`/conversations/${encodeURIComponent(id)}`),
  deleteConversation: id => postJson('/conversations/delete', { id }),
  exportConversations: () => getJson('/conversations/export'),
  importConversations: conversations => postJson('/conversations/import', { conversations }),

  listModels: () => getJson('/models'),
  listCollections: () => getJson('/collections'),
  helpGroups: () => getJson('/help-groups'),
  collectionMetadata: collection => getJson('/collection-metadata', { collection }),
  ragQuery: params => getJson('/rag-query', params),

  // Resposta em streaming (SSE): devolve o Response cru, lido por readSseStream.
  ask: (body, signal) => post('/ask', body, { signal }),
  cancelAsk: requestId => post('/ask/cancel', { request_id: requestId }).catch(() => {}),
  forgetTurn: turnId => post('/ask/forget-turn', { turn_id: turnId }).catch(() => {}),

  // Envio de PDFs: XHR em vez de fetch, porque só ele informa o progresso
  // do upload (onProgress recebe de 0 a 1).
  uploadPdfs: (files, onProgress) => new Promise((resolve, reject) => {
    const form = new FormData();
    files.forEach(file => form.append('files', file));
    const xhr = new XMLHttpRequest();
    xhr.open('POST', '/upload');
    xhr.responseType = 'json';
    xhr.upload.onprogress = e => { if (e.lengthComputable) onProgress?.(e.loaded / e.total); };
    xhr.onload = () => {
      if (xhr.status === 401) window.location.reload();  // ver checkSession
      resolve(xhr.response || { ok: false, error: `Falha no envio (HTTP ${xhr.status}).` });
    };
    xhr.onerror = () => reject(new Error('Falha na conexão com o servidor.'));
    xhr.send(form);
  }),
  uploadJobs: () => getJson('/upload/jobs'),
  documents: () => getJson('/documents'),
  deleteDocument: source => postJson('/documents/delete', { source })
};
