// Componente: popup de login (templates/base.html), carregado só sem login.
// Abre sozinho ao carregar a tela e pelos botões "Entrar" (data-open-login).
// Com o cadastro por convite ligado (INVITE_CODE no servidor), alterna para
// "Criar conta". Deu certo: recarrega a página, já logado.
import { Api } from '../services/api.js';

const byId = id => document.getElementById(id);

const dialog = byId('login-dialog');
const form = byId('login-form');
const title = byId('login-title');
const email = byId('login-email');
const password = byId('login-password');
const password2 = byId('login-password2');
const invite = byId('login-invite');
const registerFields = byId('register-fields');
const errorBox = byId('login-error');
const submitBtn = byId('login-submit');
const modeBtn = byId('login-mode-btn');

let registering = false;

function showError(message) {
  errorBox.textContent = message;
  errorBox.hidden = !message;
}

function setMode(register) {
  registering = register;
  title.textContent = register ? 'Criar conta' : 'Entrar';
  submitBtn.textContent = register ? 'Criar conta' : 'Entrar';
  // Diz ao gerenciador de senhas do navegador se é para sugerir uma senha nova.
  password.autocomplete = register ? 'new-password' : 'current-password';
  if (registerFields) registerFields.hidden = !register;
  if (modeBtn) modeBtn.textContent = register ? 'Já tem conta? Entrar' : 'Recebeu um convite? Criar conta';
  showError('');
}

function open() {
  if (!dialog.open) dialog.showModal();
  (email.value ? password : email).focus();
}

async function submit(e) {
  e.preventDefault();
  if (!email.value.trim() || !password.value) {
    showError('Informe o e-mail e a senha.');
    return;
  }
  if (registering && password.value !== password2.value) {
    showError('As senhas não conferem.');
    return;
  }

  submitBtn.disabled = true;
  showError('');
  try {
    const result = registering
      ? await Api.register(email.value, password.value, invite.value)
      : await Api.login(email.value, password.value);
    if (result.ok) {
      window.location.reload();
      return;
    }
    showError(result.error || 'Não foi possível entrar.');
    password.select();
  } catch (err) {
    showError('Falha na conexão com o servidor.');
  } finally {
    submitBtn.disabled = false;
  }
}

form.addEventListener('submit', submit);
modeBtn?.addEventListener('click', () => setMode(!registering));
document.querySelectorAll('[data-open-login]').forEach(btn => btn.addEventListener('click', open));

setMode(false);
open();
