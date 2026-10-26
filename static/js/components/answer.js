// Componente: renderização do texto do modelo (resposta, raciocínio, erros)
// em HTML — markdown simples, tabelas, blocos de código, fórmulas (MathJax),
// caminhos de imagem local e figuras/equações recuperadas do RAG.
import { IMAGE_PATH_RE, escapeHtml, localImageTag } from '../services/text.js';

// As duas menções levam junto a pontuação que vem logo depois ("Figura 2.1.",
// "Equação (3.2),"): a figura/equação é inserida depois dela, então a frase
// fecha antes do bloco em vez de sobrar um ponto solto embaixo dele.
const FIGURE_MENTION_RE = /\bFig(?:ura)?\.?\s*\d+(?:\.\d+)?[.,;:!?]?/gi;
// "Equação (3.2)", "Eq. 5", "equations 2" — mesmo padrão de
// _EQUATION_MENTION_RE em services/retrieval_service.py.
const EQUATION_MENTION_RE = /\b(?:equa[çc][ãa]o|equa[çc][õo]es|equations?|eqs?)\.?\s*\(?\s*(\d+(?:\.\d+)*[a-z]?)\b\)?[.,;:!?]?/gi;
const TAG_RE = /\\tag\*?\s*\{([^{}]+)\}/;

// Número da equação (\tag{N}) de um bloco de fórmula, ou null.
function equationTagOf(math) {
  const m = TAG_RE.exec(math || '');
  return m ? m[1].trim().replace(/^\(|\)$/g, '') : null;
}

function figureNumberOf(str) {
  const m = /\d+(?:\.\d+)?/.exec(str || '');
  return m ? m[0] : null;
}

function renderMarkdownTables(text) {
  const lines = text.split('\n');
  const out = [];
  let i = 0;

  const cleanRow = line => line.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map(cell => cell.trim());
  const isSeparator = line => {
    const trimmed = line.trim();
    return trimmed.includes('|') && /^[:\-\s|]+$/.test(trimmed) && /[-:]/.test(trimmed);
  };
  const parseAlign = line => cleanRow(line).map(cell => {
    if (/^:-+:$/.test(cell)) return 'center';
    if (/^:-+$/.test(cell)) return 'left';
    if (/^-+:$/.test(cell)) return 'right';
    return null;
  });

  const renderTable = (headers, align, rows) => {
    const th = headers.map((h, idx) =>
      `<th${align[idx] ? ` style="text-align:${align[idx]}"` : ''}>${h}</th>`
    ).join('');
    const trs = rows.map(row =>
      `<tr>${row.map((cell, idx) =>
        `<td${align[idx] ? ` style="text-align:${align[idx]}"` : ''}>${cell}</td>`
      ).join('')}</tr>`
    ).join('');
    return `<table><thead><tr>${th}</tr></thead><tbody>${trs}</tbody></table>`;
  };

  while (i < lines.length) {
    const line = lines[i];
    const next = lines[i + 1] || '';
    if (line.includes('|') && isSeparator(next)) {
      const headers = cleanRow(line);
      const align = parseAlign(next);
      const rows = [];
      i += 2;
      while (i < lines.length && lines[i].includes('|') && !isSeparator(lines[i]) && lines[i].trim() !== '' && !lines[i].trim().startsWith('---')) {
        rows.push(cleanRow(lines[i]));
        i += 1;
      }
      out.push(renderTable(headers, align, rows));
      continue;
    }
    out.push(line);
    i += 1;
  }

  return out.join('\n');
}

const figPlaceholder = idx => `@@FIGURE_TAG_${idx}@@`;

// Converte o texto em HTML deixando as figuras como marcadores (@@FIGURE_TAG_n@@)
// para quem chama decidir como inseri-las. `figures`: figuras recuperadas do
// RAG a embutir no texto; `equations`: equações numeradas ({number, latex}) a
// exibir logo depois da 1ª menção a cada uma; vazio/null = nenhuma.
// `mathSlots`: fórmulas viram encaixes renderizados uma vez só (ver
// renderAnswerBox); sem ele, ficam como texto LaTeX pro typesetMath.
function decodeCore(s, figures, equations, { mathSlots = false } = {}) {
  const codeBlocks = [];
  const placeholder = idx => `@@CODE_BLOCK_${idx}@@`;

  let text = s.replace(/```([\s\S]*?)```/g, (match, code) => {
    const idx = codeBlocks.length;
    codeBlocks.push(code);
    return placeholder(idx);
  });

  // Fórmulas saem do texto antes do markdown (senão "a*b*c" vira itálico):
  // $$...$$ e \[...\] (destacadas), $...$ e \(...\) (no meio do texto).
  const mathBlocks = [];
  const mathPlaceholder = idx => `@@MATH_BLOCK_${idx}@@`;
  const keepMath = match => {
    const idx = mathBlocks.length;
    mathBlocks.push(match);
    return mathPlaceholder(idx);
  };
  text = text
    .replace(/\$\$([\s\S]+?)\$\$/g, keepMath)
    .replace(/\\\[([\s\S]+?)\\\]/g, keepMath)
    .replace(/\\\(([\s\S]+?)\\\)/g, keepMath)
    .replace(/\$([^\n$]+?)\$/g, keepMath);

  // Menção a uma equação numerada ("Equação (3.2)"): mantém o texto e
  // acrescenta a equação, renderizada a partir do LaTeX indexado — a não ser
  // que o próprio modelo já a tenha escrito (mesmo \tag{N}).
  if (equations && equations.length) {
    const shown = new Set(mathBlocks.map(equationTagOf).filter(Boolean));
    text = text.replace(EQUATION_MENTION_RE, (match, num) => {
      const eq = equations.find(e => e.number === num);
      if (!eq || shown.has(num)) return match;
      shown.add(num);
      return match + keepMath(`$$${eq.latex}$$`);
    });
  }

  const imagePaths = [];
  const imgPlaceholder = idx => `@@IMAGE_PATH_${idx}@@`;
  text = text.replace(IMAGE_PATH_RE, match => {
    const idx = imagePaths.length;
    imagePaths.push(match);
    return imgPlaceholder(idx);
  });

  const figureMarks = [];
  if (figures && figures.length) {
    const usedIdx = new Set();

    // Marcador [FIGURA] gravado pelo indexador: substitui o marcador pela imagem.
    text = text.replace(/\[FIGURA(?:\s+sem legenda)?\]([^\n]*)/gi, (match, hint) => {
      const h = (hint || '').toLowerCase().trim();
      let idx = figures.findIndex(fig => {
        const cap = (fig.caption || '').toLowerCase().trim();
        return cap && h && (h.includes(cap) || cap.includes(h));
      });
      if (idx === -1) {
        idx = figures.findIndex((_, i) => !usedIdx.has(i));
        if (idx === -1) idx = 0;
      }
      usedIdx.add(idx);
      const placeholderIdx = figureMarks.length;
      figureMarks.push(figures[idx]);
      return figPlaceholder(placeholderIdx);
    });

    // Menção direta no texto ("Figura 2.34", "Fig. 5"): mantém o texto e
    // acrescenta a imagem logo abaixo, sem remover a citação do LLM.
    const figureCaptionNums = figures.map(fig => figureNumberOf(fig.caption));
    text = text.replace(FIGURE_MENTION_RE, match => {
      const num = figureNumberOf(match);
      if (!num) return match;
      const idx = figureCaptionNums.findIndex((n, i) => n === num && !usedIdx.has(i));
      if (idx === -1) return match;
      usedIdx.add(idx);
      const placeholderIdx = figureMarks.length;
      figureMarks.push(figures[idx]);
      return `${match}${figPlaceholder(placeholderIdx)}`;
    });
  }

  text = text
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/^(#{3,})\s*(.+?)\n/gm, '<h3>$2</h3>')
    .replace(/^(#{1,2})\s*(.+?)\n/gm, '<h2>$2</h2>')
    .replace(/^\s*---\s*$/gm, '<hr>')
    .replace(/`(.+?)`/g, '<i>$1</i>')
    .replace(/\*\*(.+?)\*\*/g, '<b>$1</b>')
    .replace(/\*(.+?)\*/g, '<i>$1</i>');

  text = renderMarkdownTables(text);
  text = text.replace(/\n/g, '<br>');

  // Substituição por função, não por string: numa string de substituição,
  // "$$" vira "$" (e "$&", "$1"... viram o trecho casado) — o que transformava
  // toda fórmula $$...$$ em $...$ e corrompia código com "$".
  imagePaths.forEach((p, idx) => {
    const escapedPath = escapeHtml(p);
    text = text.replace(imgPlaceholder(idx), () => `${escapedPath}<br>${localImageTag(p, escapedPath)}`);
  });

  codeBlocks.forEach((code, idx) => {
    text = text.replace(placeholder(idx), () => `<pre>${code}</pre>`);
  });

  mathBlocks.forEach((math, idx) => {
    text = text.replace(mathPlaceholder(idx), () => (mathSlots ? mathSlotHtml(math) : escapeHtml(math)));
  });

  return { html: text, figureMarks };
}

function figureNode(fig) {
  const wrap = document.createElement('span');
  wrap.style.display = 'contents';
  wrap.dataset.segType = 'figure';
  wrap.dataset.figKey = fig.path;
  const caption = fig.caption ? escapeHtml(fig.caption) : 'Figura';
  const captionHtml = fig.caption ? `<span class="answer-figure-caption">${caption}</span>` : '';
  // A imagem fica numa moldura própria que rola na horizontal quando ela é
  // mais larga que a resposta (tamanho e rolagem: .answer-figure no CSS da
  // página); a moldura é bloco, então já começa numa linha nova.
  wrap.innerHTML = `<span class="answer-figure">${localImageTag(fig.path, caption, '')}</span>${captionHtml}`;
  return wrap;
}

// Texto -> HTML completo, com as figuras já embutidas.
export function decode(s, figures, equations) {
  const { html, figureMarks } = decodeCore(s, figures, equations);
  let text = html;
  figureMarks.forEach((fig, idx) => {
    text = text.replace(figPlaceholder(idx), () => figureNode(fig).outerHTML);
  });
  return text;
}

// --- Fórmulas renderizadas uma vez só ------------------------------------------
// Mesmo princípio das figuras: a resposta chega em pedaços e o texto é refeito
// a cada um; se o MathJax renderizasse tudo de novo a cada pedaço, cada fórmula
// voltaria a ser LaTeX cru por um instante (a tela pisca e o layout pula). Aqui
// cada fórmula distinta (o texto-fonte, com delimitadores) é renderizada uma
// única vez e guardada; os encaixes (.math-slot) recebem uma cópia do nó pronto,
// na hora, sem passar pelo LaTeX cru. Funciona dentro de negrito, título ou
// tabela, porque o HTML do texto continua inteiro, sem ser fatiado.
const mathIds = new Map();     // fonte -> id do encaixe
const mathSources = new Map(); // id -> fonte
const mathCache = new Map();   // id -> nó já renderizado pelo MathJax
const mathPending = new Set(); // ids sendo renderizados agora
let mathJaxReady = null;

function mathIdOf(source) {
  if (!mathIds.has(source)) {
    mathIds.set(source, mathIds.size + 1);
    mathSources.set(mathIds.get(source), source);
  }
  return mathIds.get(source);
}

// Até a fórmula ser renderizada (ou se o MathJax nem carregar), o encaixe
// mostra o próprio LaTeX. `data-source` guarda o LaTeX pra quando o HTML for
// reaproveitado fora desta sessão (ver restoreMath e answer-export.js).
function mathSlotHtml(source) {
  const attr = escapeHtml(source).replace(/"/g, '&quot;');
  return `<span class="math-slot" data-math-id="${mathIdOf(source)}" data-source="${attr}">${escapeHtml(source)}</span>`;
}

// Conversa salva reaberta: o HTML guardado traz as fórmulas já renderizadas,
// mas o CSS dos símbolos que o MathJax gera não vem junto (só existe na sessão
// em que elas foram renderizadas). Volta cada encaixe ao LaTeX e renderiza de
// novo — uma vez por fórmula, pelo mesmo cache.
export function restoreMath(root) {
  root.querySelectorAll('.math-slot[data-source]').forEach(slot => {
    const source = slot.dataset.source;
    slot.dataset.mathId = mathIdOf(source);
    slot.classList.remove('rendered');
    slot.textContent = source;
  });
  fillMathSlots(root);
}

function parseMathSource(source) {
  if (source.startsWith('$$')) return { tex: source.slice(2, -2), display: true };
  if (source.startsWith('\\[')) return { tex: source.slice(2, -2), display: true };
  if (source.startsWith('\\(')) return { tex: source.slice(2, -2), display: false };
  return { tex: source.slice(1, -1), display: false };  // $...$
}

// O script do MathJax carrega de forma assíncrona (CDN): espera ele ficar pronto.
function whenMathJaxReady() {
  if (!mathJaxReady) {
    mathJaxReady = new Promise(resolve => {
      const check = () => (window.MathJax && MathJax.tex2chtmlPromise && MathJax.startup
        ? MathJax.startup.promise.then(() => resolve(MathJax))
        : setTimeout(check, 100));
      check();
    });
  }
  return mathJaxReady;
}

function fillSlot(slot, node) {
  slot.replaceChildren(node.cloneNode(true));
  slot.classList.add('rendered');
}

async function renderMath(id, source) {
  mathPending.add(id);
  try {
    const mj = await whenMathJaxReady();
    const { tex, display } = parseMathSource(source);
    const node = await mj.tex2chtmlPromise(tex, { display });
    // Gera o CSS das letras/símbolos novos dessa fórmula.
    mj.startup.document.clear();
    mj.startup.document.updateDocument();
    mathCache.set(id, node);
    document.querySelectorAll(`.math-slot[data-math-id="${id}"]:not(.rendered)`).forEach(slot => fillSlot(slot, node));
  } catch (e) {
    // fórmula fica como LaTeX: melhor que sumir
  } finally {
    mathPending.delete(id);
  }
}

function fillMathSlots(root) {
  root.querySelectorAll('.math-slot:not(.rendered)').forEach(slot => {
    const id = Number(slot.dataset.mathId);
    if (mathCache.has(id)) {
      fillSlot(slot, mathCache.get(id));
    } else if (!mathPending.has(id) && mathSources.has(id)) {
      renderMath(id, mathSources.get(id));
    }
  });
}

// Renderiza a resposta incrementalmente: em vez de recriar todo o innerHTML a
// cada chunk (o que faz as imagens de figuras piscarem, já que <img> reinserida
// via innerHTML recarrega), atualiza só os trechos de texto e reaproveita o nó
// <img> já existente quando a figura naquela posição não mudou. As fórmulas
// entram já renderizadas (ver "Fórmulas renderizadas uma vez só" acima): quem
// chama não precisa (e não deve) chamar typesetMath depois.
export function renderAnswerBox(container, rawText, figures, equations) {
  const { html, figureMarks } = decodeCore(rawText, figures, equations, { mathSlots: true });
  const segRe = /@@FIGURE_TAG_(\d+)@@/g;
  const segments = [];
  let last = 0, m;
  while ((m = segRe.exec(html))) {
    segments.push({ type: 'text', html: html.slice(last, m.index) });
    segments.push({ type: 'figure', fig: figureMarks[Number(m[1])] });
    last = segRe.lastIndex;
  }
  segments.push({ type: 'text', html: html.slice(last) });

  segments.forEach((seg, i) => {
    let el = container.children[i];
    if (seg.type === 'text') {
      if (!el || el.dataset.segType !== 'text') {
        const span = document.createElement('span');
        span.style.display = 'contents';
        span.dataset.segType = 'text';
        if (el) container.replaceChild(span, el); else container.appendChild(span);
        el = span;
      }
      // Compara com o HTML-fonte guardado, não com o innerHTML: depois que os
      // encaixes recebem as fórmulas renderizadas, o innerHTML já é outro.
      if (el.sourceHtml !== seg.html) {
        el.innerHTML = seg.html;
        el.sourceHtml = seg.html;
        fillMathSlots(el);
      }
    } else if (!(el && el.dataset.segType === 'figure' && el.dataset.figKey === seg.fig.path)) {
      const node = figureNode(seg.fig);
      if (el) container.replaceChild(node, el); else container.appendChild(node);
    }
  });

  while (container.children.length > segments.length) {
    container.removeChild(container.lastChild);
  }
}

export function typesetMath(el) {
  if (window.MathJax && MathJax.typesetPromise) {
    MathJax.typesetPromise([el]).catch(() => {});
  }
}
