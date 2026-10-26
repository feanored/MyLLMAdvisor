// Demo estático (GitHub Pages): substitui o backend Flask por respostas
// simuladas no próprio navegador. Carregado ANTES dos módulos da página
// (script clássico no <head>, ver build_demo.py) e só no demo — o app de
// verdade nunca carrega este arquivo. Intercepta:
//   fetch: /models, /help-groups, /documents, /upload/jobs, /ask (stream SSE
//          no mesmo formato de _format_sse_event em rag_web.py), /ask/cancel,
//          /ask/forget-turn
//   XMLHttpRequest: POST /upload (progresso do envio + fila de indexação)
// Nenhum arquivo sai do navegador: o "upload" só registra nome e tamanho.
(function () {
  'use strict';

  const R = String.raw;

  // --- Conteúdo de exemplo (escrito para o demo, não tirado de nenhuma obra) ---

  const documents = [
    'Notas de Aula - Análise de Fourier.pdf',
    'Sinais e Sistemas - Capítulo 2.pdf',
    'Artigo - Métodos Espectrais para EDPs.pdf'
  ];

  const EXAMPLE_QUESTION = 'O que é a transformada de Fourier e por que ela é útil?';

  const FIGURE = {
    path: 'demo/assets/figura-espectro.svg',
    caption: 'Figura 2.1: Sinal medido (acima) e o módulo do seu espectro (abaixo), com picos em 5 Hz e 12 Hz.'
  };

  const EQUATIONS = [
    { number: '2.3', latex: R`\hat f(\xi) = \int_{-\infty}^{\infty} f(x)\, e^{-2\pi i x \xi}\, dx \tag{2.3}`,
      source: 'Notas de Aula - Análise de Fourier.pdf', page: 14 },
    { number: '2.4', latex: R`f(x) = \int_{-\infty}^{\infty} \hat f(\xi)\, e^{2\pi i x \xi}\, d\xi \tag{2.4}`,
      source: 'Notas de Aula - Análise de Fourier.pdf', page: 15 },
    { number: '2.7', latex: R`(f * g)(x) = \int_{-\infty}^{\infty} f(t)\, g(x - t)\, dt \tag{2.7}`,
      source: 'Sinais e Sistemas - Capítulo 2.pdf', page: 41 }
  ];

  const FIRST_ANSWER = R`A transformada de Fourier reescreve um sinal $f(x)$ como uma soma contínua de senoides: em vez de dizer *quanto vale* o sinal em cada ponto, ela diz *quanto de cada frequência* $\xi$ ele contém.

Nas notas de aula (Notas de Aula - Análise de Fourier.pdf, p. 14), ela é definida pela Equação (2.3).

Nela, $\hat f(\xi)$ é a amplitude complexa associada à frequência $\xi$.

**Por que ela é útil**

1. **Derivadas viram multiplicações.** No domínio da frequência, derivar equivale a multiplicar por $2\pi i \xi$, o que transforma equações diferenciais lineares em equações algébricas (Artigo - Métodos Espectrais para EDPs.pdf, p. 3).
2. **Convolução vira produto.** Filtrar um sinal, que é uma convolução, passa a ser um produto ponto a ponto: $\widehat{f * g} = \hat f \cdot \hat g$ (Sinais e Sistemas - Capítulo 2.pdf, p. 41).
3. **O espectro revela estrutura escondida.** Um sinal que parece ruído no tempo pode ter poucas frequências dominantes: compare o sinal medido e os dois picos do seu espectro na Figura 2.1.

Nada se perde na mudança de domínio: o sinal original é recuperado pela transformada inversa, a Equação (2.4).

**Ressalva dos próprios textos:** para sinais amostrados usa-se a versão discreta (DFT), calculada pela FFT em $O(N \log N)$; nela aparece o *aliasing* quando a taxa de amostragem é menor que o dobro da maior frequência do sinal (Sinais e Sistemas - Capítulo 2.pdf, p. 52).`;

  const FOLLOW_UP_ANSWER = R`Aprofundando o item 2, o teorema da convolução.

A convolução de dois sinais é definida pela Equação (2.7).

O teorema diz que a transformada dela é o produto das transformadas:

$$\widehat{f * g}(\xi) = \hat f(\xi)\, \hat g(\xi)$$

Na prática, isso troca uma integral para cada ponto de saída, de custo $O(N^2)$ no caso discreto, por duas FFTs, um produto e uma FFT inversa, de custo $O(N \log N)$ (Sinais e Sistemas - Capítulo 2.pdf, p. 43). É por isso que filtros longos, como reverberação de áudio ou desfoque de imagens, costumam ser aplicados no domínio da frequência.`;

  // --- Utilitários ---------------------------------------------------------------

  const realFetch = window.fetch.bind(window);
  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
  const json = data => new Response(JSON.stringify(data), { headers: { 'Content-Type': 'application/json' } });
  const uid = () => Math.random().toString(36).slice(2) + Date.now().toString(36);

  // Rota da API a partir da URL (o app usa caminhos absolutos, "/ask"; no
  // GitHub Pages eles apontam pra raiz do domínio, então vale o fim do caminho).
  const ROUTES = ['upload/jobs', 'ask/cancel', 'ask/forget-turn', 'help-groups', 'documents', 'models', 'upload', 'ask'];
  function routeOf(input) {
    const raw = typeof input === 'string' ? input : (input && input.url) || '';
    const path = new URL(raw, location.href).pathname.replace(/\/+$/, '');
    return ROUTES.find(route => path === '/' + route || path.endsWith('/' + route)) || null;
  }

  function sseEvent(name, payload) {
    const data = typeof payload === 'string' ? payload.replace(/\n/g, '\ndata: ') : JSON.stringify(payload);
    return `event: ${name}\ndata: ${data}\n\n`;
  }

  const STOPWORDS = new Set(['que', 'qual', 'quais', 'como', 'por', 'para', 'ela', 'ele', 'uma', 'dos', 'das',
    'com', 'sobre', 'isso', 'esse', 'essa', 'mais', 'melhor', 'explique', 'detalhe', 'útil', 'util']);
  function keywordsOf(question) {
    const words = (question.match(/[\p{L}\d][\p{L}\d.-]*/gu) || [])
      .filter(w => w.length >= 3 && !STOPWORDS.has(w.toLowerCase()));
    return [...new Set(words)].slice(0, 6);
  }

  // --- /ask: resposta em streaming -------------------------------------------------

  function askResponse(body, signal) {
    const isFirstTurn = body.is_first_turn !== false;
    const answer = isFirstTurn ? FIRST_ANSWER : FOLLOW_UP_ANSWER;
    const encoder = new TextEncoder();
    let aborted = false;

    const stream = new ReadableStream({
      async start(controller) {
        const abort = () => {
          aborted = true;
          try { controller.error(new DOMException('A requisição foi cancelada.', 'AbortError')); } catch (e) { /* já fechado */ }
        };
        if (signal) {
          if (signal.aborted) return abort();
          signal.addEventListener('abort', abort, { once: true });
        }
        const send = (name, payload) => { if (!aborted) controller.enqueue(encoder.encode(sseEvent(name, payload))); };

        await sleep(500);
        if (isFirstTurn) send('keywords', keywordsOf(String(body.question || '')));
        send('figures', [FIGURE]);
        send('equations', EQUATIONS);
        await sleep(700);  // "buscando na base e pensando"

        // Pedaços de poucas palavras, como chegam do modelo.
        const pieces = answer.match(/\S+\s*/g) || [];
        for (let i = 0; i < pieces.length && !aborted; i += 3) {
          send('content', pieces.slice(i, i + 3).join(''));
          await sleep(35);
        }
        if (aborted) return;
        send('done', 'ok');
        controller.close();
      }
    });
    return new Response(stream, { headers: { 'Content-Type': 'text/event-stream' } });
  }

  // --- /upload: fila de indexação simulada --------------------------------------------

  const jobs = [];       // mais recente primeiro
  let busyUntil = 0;     // a fila indexa um PDF por vez, como o IndexingService
  let firstJob = true;

  function stepsFor(file) {
    const steps = [];
    if (firstJob) steps.push(['Carregando o Docling e os modelos (só no primeiro envio)', 2000]);
    firstJob = false;
    steps.push(
      [`Convertendo ${file}`, 2500],
      ['Convertendo fórmulas para LaTeX (1/12)', 1500],
      ['Convertendo fórmulas para LaTeX (10/12)', 1200],
      ['12/12 fórmulas convertidas para LaTeX (5 numeradas)', 800],
      ['Dividindo o texto em trechos', 900],
      ['Descrevendo figuras (1/4)', 1300],
      ['Descrevendo figuras (2/4)', 1300],
      ['Descrevendo figuras (3/4)', 1300],
      ['Descrevendo figuras (4/4)', 1300],
      ['Gravando na base', 1000]
    );
    return steps;
  }

  function submitFiles(files) {
    const now = Date.now();
    const created = [], rejected = [];
    for (const file of files) {
      const name = file.name;
      if (!name.toLowerCase().endsWith('.pdf')) {
        rejected.push({ file: name, reason: 'Não é um arquivo .pdf.' });
        continue;
      }
      if (jobs.some(job => job.file === name && jobState(job).status in { queued: 1, running: 1 })) {
        rejected.push({ file: name, reason: 'Esse PDF já está na fila de indexação.' });
        continue;
      }
      const start = Math.max(now, busyUntil);
      const already = documents.includes(name);
      const steps = already ? [] : stepsFor(name);
      const duration = already ? 1000 : steps.reduce((sum, [, ms]) => sum + ms, 0);
      busyUntil = start + duration;
      const job = { id: uid(), file: name, start, end: start + duration, steps, already,
                    chunks: 60 + Math.floor(Math.random() * 120), counted: false };
      jobs.unshift(job);
      created.push(publicJob(job));
    }
    return { ok: true, jobs: created, rejected };
  }

  function jobState(job, now = Date.now()) {
    if (now < job.start) return { status: 'queued', message: 'Na fila' };
    if (now >= job.end) {
      if (job.already) return { status: 'skipped', message: 'Esse PDF já está na base, sem alterações.' };
      if (!job.counted) {  // entrou na base: aparece no menu lateral e no Orientador
        job.counted = true;
        if (!documents.includes(job.file)) documents.push(job.file);
        documents.sort((a, b) => a.localeCompare(b, 'pt-BR'));
      }
      return { status: 'done', message: `${job.chunks} trechos indexados` };
    }
    let t = job.start;
    for (const [message, ms] of job.steps) {
      t += ms;
      if (now < t) return { status: 'running', message };
    }
    return { status: 'running', message: 'Preparando a indexação' };
  }

  function publicJob(job) {
    const now = Date.now();
    const state = jobState(job, now);
    const elapsed = now < job.start ? null : Math.round((Math.min(now, job.end) - job.start) / 100) / 10;
    return { id: job.id, file: job.file, status: state.status, message: state.message,
             chunks: state.status === 'done' ? job.chunks : null, elapsed };
  }

  // --- fetch ---------------------------------------------------------------------------

  window.fetch = async function (input, init = {}) {
    const route = routeOf(input);
    if (!route) return realFetch(input, init);
    const method = (init.method || 'GET').toUpperCase();
    let body = {};
    try { body = init.body ? JSON.parse(init.body) : {}; } catch (e) { /* corpo não-JSON */ }

    switch (route) {
      case 'models': return json(['qwen3:8b', 'qwen3:14b', 'qwen3.5:latest']);
      case 'help-groups':
        jobs.forEach(job => jobState(job));
        return json(Object.fromEntries(documents.map(name => [
          name, { label: name.replace(/\.pdf$/i, '').replace(/_/g, ' '), sources: [name], default_checked: false }
        ])));
      case 'documents':
        jobs.forEach(job => jobState(job));
        return json([...documents]);
      case 'upload/jobs': return json(jobs.map(publicJob));
      case 'ask/cancel':
      case 'ask/forget-turn': return json({ ok: true });
      case 'ask':
        if (method !== 'POST') break;
        return askResponse(body, init.signal);
      default: break;
    }
    return realFetch(input, init);
  };

  // --- XMLHttpRequest (só o POST /upload; o resto segue normal) ------------------------------

  const realOpen = XMLHttpRequest.prototype.open;
  const realSend = XMLHttpRequest.prototype.send;

  XMLHttpRequest.prototype.open = function (method, url, ...rest) {
    this.__demoUpload = String(method).toUpperCase() === 'POST' && routeOf(url) === 'upload';
    if (!this.__demoUpload) return realOpen.call(this, method, url, ...rest);
  };

  XMLHttpRequest.prototype.send = function (body) {
    if (!this.__demoUpload) return realSend.call(this, body);
    const files = body && typeof body.getAll === 'function' ? body.getAll('files') : [];
    const total = files.reduce((sum, file) => sum + (file.size || 0), 0) || 1;
    const xhr = this;
    let sent = 0;
    // Envio simulado: ~1,5 s, com eventos de progresso como os de um upload real.
    const tick = () => {
      sent = Math.min(total, sent + total / 12);
      if (xhr.upload && typeof xhr.upload.onprogress === 'function') {
        xhr.upload.onprogress({ lengthComputable: true, loaded: sent, total });
      }
      if (sent < total) return setTimeout(tick, 120);
      Object.defineProperty(xhr, 'status', { value: 200, configurable: true });
      Object.defineProperty(xhr, 'readyState', { value: 4, configurable: true });
      Object.defineProperty(xhr, 'response', { value: submitFiles(files), configurable: true });
      if (typeof xhr.onload === 'function') xhr.onload();
    };
    setTimeout(tick, 120);
  };

  // --- Página ---------------------------------------------------------------------------------

  document.addEventListener('DOMContentLoaded', () => {
    const params = new URLSearchParams(location.search);
    const auto = params.has('auto');

    // ?menu=1: abre a gaveta do menu (tela estreita) — usado nos screenshots.
    if (params.has('menu')) setTimeout(() => document.getElementById('sidebar-toggle')?.click(), 400);

    // Orientador: já vem com uma pergunta de exemplo; ?auto=1 a envia sozinha.
    const question = document.getElementById('question');
    if (question && !question.value) question.value = EXAMPLE_QUESTION;
    if (question && auto) setTimeout(() => document.getElementById('btn')?.click(), 600);

    // Enviar PDFs: quem visita o demo pode não ter um PDF à mão. ?auto=1 usa o
    // exemplo e envia sozinho.
    const fileInput = document.getElementById('file-input');
    const dropzone = document.getElementById('dropzone');
    if (fileInput && dropzone) {
      const hint = document.createElement('p');
      hint.className = 'demo-sample';
      hint.innerHTML = 'Sem um PDF à mão? <button type="button" class="demo-sample-btn">Usar um PDF de exemplo</button>';
      dropzone.after(hint);
      const useSample = () => {
        const transfer = new DataTransfer();
        transfer.items.add(new File(['%PDF-1.4\n'], 'Exemplo - Séries de Fourier.pdf', { type: 'application/pdf' }));
        fileInput.files = transfer.files;
        fileInput.dispatchEvent(new Event('change'));
      };
      hint.querySelector('button').addEventListener('click', useSample);
      if (auto) {
        setTimeout(() => {
          useSample();
          setTimeout(() => document.getElementById('send-btn')?.click(), 300);
        }, 600);
      }
    }
  });
})();
