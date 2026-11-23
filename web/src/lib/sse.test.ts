import { describe, expect, it } from "vitest";
import { parseBlock, readSse } from "./sse";

function stream(chunks: string[]): ReadableStream<Uint8Array> {
  const enc = new TextEncoder();
  return new ReadableStream({
    start(controller) {
      for (const c of chunks) controller.enqueue(enc.encode(c));
      controller.close();
    },
  });
}

describe("sse", () => {
  it("parses id, event and data, skipping keepalive comments", () => {
    expect(parseBlock(": ping")).toBeNull();
    expect(parseBlock('id: 3\r\nevent: posted\r\ndata: {"a":1}'.replace(/\r\n/g, "\n"))).toEqual({ id: 3, event: "posted", data: '{"a":1}' });
  });

  it("handles messages split across chunks, including a CRLF split in two", async () => {
    const out = [];
    for await (const m of readSse(stream(["id: 1\r\nevent: a\r\ndata: {}\r", "\n\r\n: ping\r\n\r\nid: 2\r\nev", "ent: b\r\ndata: {\"x\":2}\r\n\r\n"]))) out.push(m);
    expect(out).toEqual([
      { id: 1, event: "a", data: "{}" },
      { id: 2, event: "b", data: '{"x":2}' },
    ]);
  });
});
