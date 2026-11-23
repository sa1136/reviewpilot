// Minimal Server Sent Events parser over a fetch body. We read the stream with fetch rather than
// EventSource so we control reconnects, backoff and the Last-Event-ID header ourselves.

export type SseMessage = { id: number | null; event: string; data: string };

export async function* readSse(body: ReadableStream<Uint8Array>): AsyncGenerator<SseMessage> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      // Normalize line endings, but keep a trailing \r: its \n may arrive in the next chunk.
      const tail = buffer.endsWith("\r") ? "\r" : "";
      buffer = buffer.slice(0, buffer.length - tail.length).replace(/\r\n?/g, "\n") + tail;
      let end: number;
      while ((end = buffer.indexOf("\n\n")) !== -1) {
        const block = buffer.slice(0, end);
        buffer = buffer.slice(end + 2);
        const message = parseBlock(block);
        if (message) yield message;
      }
    }
  } finally {
    reader.releaseLock();
  }
}

export function parseBlock(block: string): SseMessage | null {
  let id: number | null = null;
  let event = "message";
  const data: string[] = [];
  for (const line of block.split("\n")) {
    if (!line || line.startsWith(":")) continue; // keepalive comment
    const colon = line.indexOf(":");
    const field = colon === -1 ? line : line.slice(0, colon);
    let value = colon === -1 ? "" : line.slice(colon + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    if (field === "id") id = Number(value);
    else if (field === "event") event = value;
    else if (field === "data") data.push(value);
  }
  if (!data.length) return null;
  return { id, event, data: data.join("\n") };
}
