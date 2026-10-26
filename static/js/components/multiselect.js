// Componente: combo de seleção múltipla com checkboxes (botão + painel), com
// uma opção "Todos". Cada item aceita string crua (valor = rótulo) ou objeto
// {value, label} quando o rótulo exibido difere do valor interno.

export class MultiSelect {
  constructor({ wrap, button, panel }) {
    this.wrap = wrap;
    this.button = button;
    this.panel = panel;
    this.items = [];
    this.selected = new Set();
    this.allCheckbox = null;

    button.addEventListener('click', e => {
      e.stopPropagation();
      this.toggle();
    });
    document.addEventListener('click', e => {
      if (!wrap.contains(e.target)) this.toggle(true);
    });
  }

  // Limpa as opções e deixa o botão desativado mostrando `text`.
  reset(text) {
    this.items = [];
    this.selected = new Set();
    this.allCheckbox = null;
    this.panel.hidden = true;
    this.panel.innerHTML = '';
    this.button.textContent = text;
    this.button.disabled = true;
  }

  // `preselected`: valores já marcados ao renderizar.
  setValues(items, preselected = []) {
    this.items = items.map(it => (typeof it === 'string' ? { value: it, label: it } : it));
    this.selected = new Set(preselected);
    this.render();
    this.updateButton();
    this.button.disabled = false;
  }

  // Seleção vazia ou completa equivale a "sem filtro".
  payload() {
    const n = this.selected.size;
    if (n === 0 || n === this.items.length) return null;
    return [...this.selected];
  }

  singleValue() {
    return this.selected.size === 1 ? [...this.selected][0] : null;
  }

  toggle(forceClose) {
    if (this.button.disabled) return;
    const show = forceClose === true ? false : this.panel.hidden;
    this.panel.hidden = !show;
  }

  updateButton() {
    const total = this.items.length;
    const n = this.selected.size;
    if (n === 0 || n === total) {
      this.button.textContent = '- todas -';
    } else if (n === 1) {
      const value = [...this.selected][0];
      this.button.textContent = this.items.find(it => it.value === value)?.label ?? value;
    } else {
      this.button.textContent = `${n} selecionadas`;
    }
  }

  syncAllCheckbox() {
    if (!this.allCheckbox) return;
    const total = this.items.length;
    const n = this.selected.size;
    this.allCheckbox.checked = n === total;
    this.allCheckbox.indeterminate = n > 0 && n < total;
  }

  onChange() {
    this.syncAllCheckbox();
    this.updateButton();
  }

  render() {
    this.panel.innerHTML = '';

    const allLabel = document.createElement('label');
    allLabel.className = 'msel-option msel-all';
    allLabel.innerHTML = '<input type="checkbox"> Todos';
    this.allCheckbox = allLabel.querySelector('input');
    this.allCheckbox.addEventListener('change', () => {
      const checked = this.allCheckbox.checked;
      this.selected = checked ? new Set(this.items.map(it => it.value)) : new Set();
      this.panel.querySelectorAll('.msel-option:not(.msel-all) input').forEach(cb => {
        cb.checked = checked;
      });
      this.onChange();
    });
    this.panel.appendChild(allLabel);

    this.items.forEach(item => {
      const label = document.createElement('label');
      label.className = 'msel-option';
      const cb = document.createElement('input');
      cb.type = 'checkbox';
      cb.value = item.value;
      cb.checked = this.selected.has(item.value);
      cb.addEventListener('change', () => {
        if (cb.checked) this.selected.add(item.value);
        else this.selected.delete(item.value);
        this.onChange();
      });
      label.appendChild(cb);
      label.appendChild(document.createTextNode(item.label));
      this.panel.appendChild(label);
    });

    this.syncAllCheckbox();
  }
}
