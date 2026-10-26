// Service: utilitários de texto/HTML compartilhados pelas duas telas.

// Caminho local de imagem citado num texto (figuras extraídas dos PDFs),
// servido pelo endpoint /local-image.
export const IMAGE_PATH_RE = /[^\s()<>",]*[\\/][^\s()<>",]*\.(?:png|jpe?g|gif|webp|bmp)\b/gi;

export function escapeHtml(s) {
  return String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

// <img> de uma imagem local; some da página se o servidor não a encontrar.
export function localImageTag(path, alt, style = 'max-width:100%;object-fit:contain;') {
  return `<img src="/local-image?path=${encodeURIComponent(path)}" alt="${alt}" style="${style}" loading="lazy" onerror="this.style.display='none'">`;
}

// Texto cru com cada caminho de imagem seguido da própria imagem renderizada.
export function textWithImagesHtml(text, imageStyle) {
  text = text || '';
  let out = '';
  let lastIndex = 0;
  let match;
  IMAGE_PATH_RE.lastIndex = 0;
  while ((match = IMAGE_PATH_RE.exec(text)) !== null) {
    out += escapeHtml(text.slice(lastIndex, match.index));
    const path = match[0];
    out += `${escapeHtml(path)}<br>${localImageTag(path, escapeHtml(path), imageStyle)}`;
    lastIndex = match.index + path.length;
  }
  return out + escapeHtml(text.slice(lastIndex));
}

export function parseJson(data, fallback) {
  try {
    return JSON.parse(data);
  } catch (e) {
    return fallback;
  }
}
