// GET /api/dict?word=hello  → 中文释义 + 英美音标

import { lookupWord } from "./_shared/dict.mjs";

export default async (req) => {
  const url = new URL(req.url);
  const word = (url.searchParams.get("word") || "").trim().toLowerCase();
  if (!/^[a-z][a-z'’\- ]{0,40}$/i.test(word)) {
    return Response.json({ error: "invalid word" }, { status: 400 });
  }
  const info = await lookupWord(word);
  return Response.json(info, {
    headers: {
      "Cache-Control": "public, max-age=86400, stale-while-revalidate=604800",
      "Access-Control-Allow-Origin": "*",
    },
  });
};

export const config = { path: "/api/dict" };
