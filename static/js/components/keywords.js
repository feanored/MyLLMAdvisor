// Componente: linha de chips com as palavras-chave que o servidor extraiu da
// pergunta e usou como consultas independentes ao RAG — mostrar quais foram
// ajuda a entender de onde veio (ou por que faltou) o contexto. Usado pelo
// chat e pela consulta bruta às bases.
import { escapeHtml } from '../services/text.js';

// Devolve a lista efetivamente mostrada (vazia se `keywords` não for lista).
export function renderKeywords(line, keywords) {
  const list = Array.isArray(keywords) ? keywords : [];
  if (!list.length) {
    line.style.display = 'none';
    line.innerHTML = '';
    return list;
  }
  const chips = list.map(k => `<span class="keyword-chip">${escapeHtml(k)}</span>`).join('');
  line.innerHTML = `<span class="keywords-label">Palavras-chave:</span>${chips}`;
  line.style.display = '';
  return list;
}
