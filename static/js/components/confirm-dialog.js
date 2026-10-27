// Componente: confirmação modal das exclusões (documento da base, conversa,
// bloco de pergunta/resposta), no lugar do window.confirm do navegador.
// Estilo: .confirm-dialog em static/layout.css.
import { escapeHtml } from '../services/text.js';

let dialog = null;

// Um <dialog> só, criado na 1ª chamada e reaproveitado.
function ensureDialog() {
  if (dialog) return dialog;
  dialog = document.createElement('dialog');
  dialog.className = 'confirm-dialog';
  dialog.setAttribute('aria-labelledby', 'confirm-dialog-title');
  dialog.innerHTML = `
    <div class="confirm-dialog-head">
      <svg class="confirm-dialog-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"
           stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
        <path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13M10 11v6M14 11v6"/></svg>
      <h2 id="confirm-dialog-title"></h2>
    </div>
    <p class="confirm-dialog-message"></p>
    <div class="row">
      <button type="button" class="btn-secondary" value="cancel">Cancelar</button>
      <button type="button" class="btn-danger" value="confirm"></button>
    </div>`;
  // Clique no fundo escurecido (fora da caixa) = cancelar.
  dialog.addEventListener('click', e => {
    if (e.target === dialog) dialog.close('cancel');
  });
  dialog.querySelectorAll('.row button').forEach(btn => {
    btn.addEventListener('click', () => dialog.close(btn.value));
  });
  document.body.appendChild(dialog);
  return dialog;
}

// Abre a confirmação e resolve true só se a pessoa clicar no botão de
// excluir; Cancelar, Esc ou clique fora resolvem false. `message` é texto
// puro; `detail` (opcional, ex.: nome do arquivo) aparece em destaque.
export function confirmDelete({ title, message, detail = '', confirmLabel = 'Excluir' }) {
  const dlg = ensureDialog();
  dlg.querySelector('h2').textContent = title;
  dlg.querySelector('.confirm-dialog-message').innerHTML =
    (detail ? `<strong class="confirm-dialog-detail">${escapeHtml(detail)}</strong>` : '') + escapeHtml(message);
  dlg.querySelector('.btn-danger').textContent = confirmLabel;
  dlg.returnValue = 'cancel';
  return new Promise(resolve => {
    dlg.addEventListener('close', () => resolve(dlg.returnValue === 'confirm'), { once: true });
    dlg.showModal();
    // Foco em Cancelar: Enter sem querer não exclui nada.
    dlg.querySelector('.btn-secondary').focus();
  });
}
