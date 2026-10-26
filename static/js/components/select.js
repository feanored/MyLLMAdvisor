// Componente: preenchimento de <select> — usado pelos combos de modelo,
// collection e metadados.

export function appendOptions(select, values, selected) {
  values.forEach(value => {
    const option = document.createElement('option');
    option.value = option.textContent = value;
    if (value === selected) option.selected = true;
    select.appendChild(option);
  });
}

// Substitui as opções por uma só, sem valor (placeholder ou mensagem de erro).
export function setSelectMessage(select, text) {
  select.innerHTML = '';
  const option = document.createElement('option');
  option.value = '';
  option.textContent = text;
  select.appendChild(option);
}

// Carrega as opções de fetchValues() (promise de uma lista de strings).
// emptyText: mensagem quando a lista vem vazia (omitido = combo vazio).
export async function loadSelectOptions(select, fetchValues, { selected, emptyText, errorText } = {}) {
  try {
    const values = await fetchValues();
    select.innerHTML = '';
    if (emptyText && values.length === 0) {
      setSelectMessage(select, emptyText);
      return;
    }
    appendOptions(select, values, selected);
  } catch (e) {
    setSelectMessage(select, errorText);
  }
}
