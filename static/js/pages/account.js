// Página: Minha conta (templates/account.html) — troca de senha num popup e
// saída da conta.
import { Api } from '../services/api.js';

const byId = id => document.getElementById(id);

const els = {
  changeBtn: byId('change-password-btn'),
  logoutBtn: byId('logout-btn'),
  status: byId('account-status'),
  dialog: byId('password-dialog'),
  form: byId('password-form'),
  current: byId('current-password'),
  next: byId('new-password'),
  next2: byId('new-password2'),
  error: byId('password-error'),
  submitBtn: byId('password-submit'),
  cancelBtn: byId('password-cancel-btn')
};

function showError(message) {
  els.error.textContent = message;
  els.error.hidden = !message;
}

function openPasswordDialog() {
  els.form.reset();
  showError('');
  els.status.textContent = '';
  els.dialog.showModal();
  els.current.focus();
}

async function changePassword(e) {
  e.preventDefault();
  if (!els.current.value || !els.next.value) {
    showError('Preencha a senha atual e a nova.');
    return;
  }
  if (els.next.value !== els.next2.value) {
    showError('As novas senhas não conferem.');
    return;
  }

  els.submitBtn.disabled = true;
  showError('');
  try {
    const result = await Api.changePassword(els.current.value, els.next.value);
    if (!result.ok) {
      showError(result.error || 'Não foi possível alterar a senha.');
      return;
    }
    els.dialog.close();
    els.status.textContent = 'Senha alterada. Os outros navegadores conectados nesta conta foram desconectados.';
  } catch (err) {
    showError('Falha na conexão com o servidor.');
  } finally {
    els.submitBtn.disabled = false;
  }
}

async function logout() {
  els.logoutBtn.disabled = true;
  try {
    await Api.logout();
  } finally {
    // O link do título leva à tela inicial (no demo do GitHub Pages, à do demo).
    window.location.href = document.querySelector('.app-title').href;
  }
}

els.changeBtn.addEventListener('click', openPasswordDialog);
els.cancelBtn.addEventListener('click', () => els.dialog.close());
els.form.addEventListener('submit', changePassword);
els.logoutBtn.addEventListener('click', logout);
