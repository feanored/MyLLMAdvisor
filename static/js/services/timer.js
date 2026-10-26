// Service: cronômetros nomeados, cada um ligado a um elemento que mostra o
// tempo decorrido ("1.23s") enquanto está rodando.

export function formatTimer(ms) {
  return `${(ms / 1000).toFixed(2)}s`;
}

export class Timers {
  // elementIds: { nome: id do elemento que mostra aquele cronômetro }
  constructor(elementIds) {
    this.elementIds = elementIds;
    this.states = {};
    Object.keys(elementIds).forEach(name => {
      this.states[name] = { interval: null, startTime: null };
    });
  }

  element(name) {
    return document.getElementById(this.elementIds[name]);
  }

  running(name) {
    return Boolean(this.states[name].startTime);
  }

  clear(name) {
    const timer = this.states[name];
    if (timer.interval) clearInterval(timer.interval);
    timer.interval = null;
    timer.startTime = null;
  }

  start(name) {
    this.clear(name);
    const timer = this.states[name];
    const el = this.element(name);
    timer.startTime = Date.now();
    if (el) {
      el.textContent = '0.00s';
      timer.interval = setInterval(() => {
        if (timer.startTime !== null) el.textContent = formatTimer(Date.now() - timer.startTime);
      }, 100);
    }
  }

  stop(...names) {
    names.forEach(name => {
      const timer = this.states[name];
      if (timer.interval) {
        clearInterval(timer.interval);
        timer.interval = null;
      }
      const el = this.element(name);
      if (el && timer.startTime !== null) el.textContent = formatTimer(Date.now() - timer.startTime);
      timer.startTime = null;
    });
  }

  reset() {
    Object.keys(this.states).forEach(name => {
      this.clear(name);
      const el = this.element(name);
      if (el) el.textContent = '-';
    });
  }
}
