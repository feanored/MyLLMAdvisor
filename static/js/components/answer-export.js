// Componente: exporta a pergunta e a resposta já renderizadas como um HTML
// standalone para baixar.
import { downloadBlob } from '../services/download.js';
import { escapeHtml } from '../services/text.js';

// As <img> da resposta apontam para /local-image, uma URL relativa ao servidor:
// num arquivo salvo e aberto via file:// elas não resolvem. Converte a imagem já
// renderizada na página em data URI, desenhando no canvas os pixels que o
// navegador decodificou — sem nenhuma requisição nova.
function inlineRenderedImages(liveRoot, cloneRoot) {
  const clones = cloneRoot.querySelectorAll('img');
  liveRoot.querySelectorAll('img').forEach((img, i) => {
    const target = clones[i];
    if (!target || !img.naturalWidth || !img.naturalHeight) return;
    try {
      const canvas = document.createElement('canvas');
      canvas.width = img.naturalWidth;
      canvas.height = img.naturalHeight;
      canvas.getContext('2d').drawImage(img, 0, 0);
      target.setAttribute('src', canvas.toDataURL('image/png'));
      // onerror/loading só fazem sentido na página ao vivo.
      target.removeAttribute('onerror');
      target.removeAttribute('loading');
    } catch (e) {
      // canvas indisponível: mantém o src original em vez de perder a figura
    }
  });
}

function renderedHtml(box) {
  const clone = box.cloneNode(true);
  inlineRenderedImages(box, clone);
  return clone.innerHTML.trim();
}

function timestamp() {
  const now = new Date();
  const pad = n => String(n).padStart(2, '0');
  return `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}_${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}`;
}

export function downloadAnswer({ answerBox, question, keywords, model, collection }) {
  if (!answerBox.innerHTML.trim()) {
    alert('Não há resposta para baixar.');
    return;
  }

  const htmlContent = renderedHtml(answerBox);

  const keywordsLine = keywords.length
    ? `<p class="keywords">Palavras-chave consultadas: ${keywords.map(escapeHtml).join(' • ')}</p>`
    : '';
  const questionSection = `<h2>Pergunta</h2><pre>${escapeHtml(question)}</pre>${keywordsLine}`;

  const sanitize = s => (s || '').replace(/[^a-zA-Z0-9_-]/g, '_');
  const nameParts = ['resposta', sanitize(model), sanitize(collection), timestamp()].filter(Boolean);

  const fullHtml = `<!DOCTYPE html>
<html lang="pt-br">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Resposta - My LLM Advisor</title>
  <script>
    window.MathJax = {
      tex: { inlineMath: [['$', '$'], ['\\\\(', '\\\\)']], displayMath: [['$$', '$$'], ['\\\\[', '\\\\]']] },
      output: { displayOverflow: 'scroll', linebreaks: { inline: true } }
    };
  <\/script>
  <script id="MathJax-script" async src="https://cdn.jsdelivr.net/npm/mathjax@4.1.3/tex-mml-chtml.js"><\/script>
  <style>
    body { font-family: system-ui, sans-serif; line-height: 1.6; color: #2d3748; padding: 24px; }
    h2, h3 { margin-top: 1rem; }
    pre { white-space: pre-wrap; background: #f7fafc; padding: 12px; border-radius: 6px; overflow: auto; }
    /* Mesmas regras da tela (rag_help.css): nada alarga a página, o que é largo rola. */
    body { overflow-wrap: break-word; }
    mjx-container[display="true"] { max-width: 100%; overflow-x: auto; overflow-y: hidden; }
    .answer-figure { display: block; max-width: 100%; overflow-x: auto; margin-top: .5rem; }
    .answer-figure img { display: block; max-width: 100%; height: auto; }
    .answer-figure-caption { display: block; font-size: .85rem; color: #718096; margin-top: .25rem; }
    @media (max-width: 760px) {
      .answer-figure img { max-width: none; width: 100%; min-width: 480px; }
      table { display: block; overflow-x: auto; width: auto; max-width: 100%; }
      th, td { min-width: 7rem; }
    }
    hr { margin: 1rem 0; }
    table { width: 100%; border-collapse: collapse; margin: 1rem 0; border: 1px solid #cbd5e0; border-radius: 10px; overflow: hidden; }
    th, td { padding: 0.75rem 1rem; border: 1px solid #e2e8f0; text-align: left; }
    th { background: #f7fafc; color: #2d3748; font-weight: 700; }
    tr:nth-child(even) td { background: #f8fafc; }
    table tbody tr td { background: #ffffff; }
    .keywords { font-size: .8rem; color: #718096; margin-top: .35rem; }
  </style>
</head>
<body>${questionSection}<h2>Resposta</h2>${htmlContent}</body>
</html>`;

  downloadBlob(fullHtml, 'text/html;charset=utf-8', `${nameParts.join('_')}.html`);
}
