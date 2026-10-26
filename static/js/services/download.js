// Service: oferece um conteúdo gerado no navegador como arquivo para baixar.

export function downloadBlob(content, mimeType, fileName) {
  const blob = new Blob([content], { type: mimeType });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = fileName;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
}

// Campo de CSV entre aspas, com as aspas internas duplicadas.
export function csvQuote(value) {
  return `"${String(value).replace(/"/g, '""')}"`;
}
