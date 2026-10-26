// Service: leitura de um stream Server-Sent Events vindo de um fetch (o
// EventSource nativo não aceita POST). O formato de fio é o de
// _format_sse_event em rag_web.py: "event: nome\ndata: ...\n\n", com texto de
// várias linhas quebrado em várias linhas "data:".

function parseEvent(part) {
  let name = 'message';
  let data = '';
  part.split('\n').forEach(line => {
    if (line.startsWith('event:')) {
      name = line.slice(6).trim();
    } else if (line.startsWith('data:')) {
      const value = line.length > 5 ? line.slice(5) : '';
      data += (value.startsWith(' ') ? value.slice(1) : value) + '\n';
    }
  });
  if (!data) return null;
  return { name, data: data.replace(/\n$/, '') };
}

// Chama onEvent(nome, data) para cada evento, na ordem. Se onEvent devolver
// false, o resto dos eventos que chegaram no mesmo pedaço do stream é
// descartado (usado para erro/cancelamento).
export async function readSseStream(response, onEvent) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  const dispatch = parts => {
    for (const part of parts) {
      const event = parseEvent(part);
      if (event && onEvent(event.name, event.data) === false) return;
    }
  };

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const parts = buffer.split('\n\n');
    buffer = parts.pop() || '';
    dispatch(parts);
  }

  const remaining = decoder.decode();
  if (remaining) {
    const parts = (buffer + remaining).split('\n\n');
    parts.pop();
    dispatch(parts);
  }
}
