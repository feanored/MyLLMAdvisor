// Componente: menu lateral em tela estreita (celular). Abaixo de COMPACT_QUERY
// o menu vira um trilho fino só com ícones (ver layout.css); o botão do topo
// o abre como gaveta por cima do conteúdo. Carregado por templates/base.html
// em todas as telas.

// Mesmo limite do @media "trilho" em static/layout.css.
const COMPACT_QUERY = window.matchMedia('(max-width: 760px)');

const app = document.querySelector('.app');
const sidebar = document.getElementById('sidebar');
const toggle = document.getElementById('sidebar-toggle');
const backdrop = document.getElementById('sidebar-backdrop');

const isOpen = () => app.classList.contains('sidebar-open');

function setOpen(open) {
  app.classList.toggle('sidebar-open', open);
  const label = open ? 'Fechar menu' : 'Abrir menu';
  toggle.setAttribute('aria-expanded', String(open));
  toggle.setAttribute('aria-label', label);
  toggle.title = label;
}

toggle.addEventListener('click', () => setOpen(!isOpen()));
backdrop.addEventListener('click', () => setOpen(false));
document.addEventListener('keydown', e => {
  // Com um modal aberto por cima da gaveta (ex.: confirmação de exclusão),
  // o Esc fecha só o modal.
  if (e.key === 'Escape' && isOpen() && !document.querySelector('dialog[open]')) setOpen(false);
});

// Escolher algo no menu (outra tela, uma conversa, "Nova conversa") fecha a
// gaveta, pra mostrar o resultado. Tela nova: marque com data-close-sidebar
// os elementos do bloco "sidebar" que devem fechá-la ao serem clicados.
sidebar.addEventListener('click', e => {
  if (!COMPACT_QUERY.matches || !isOpen()) return;
  if (e.target.closest('.conversation-delete')) return;
  if (e.target.closest('a, .conversation-item, .new-conversation-btn, [data-close-sidebar]')) setOpen(false);
});

// Girar o celular ou alargar a janela: volta ao menu normal, fechado.
COMPACT_QUERY.addEventListener('change', () => setOpen(false));
