// GET /api/audio?word=hello&accent=uk  → 经过校验的真实发音音频
//
// 校验通过才返回音频；确无录音返回 404 + {retryable:false}，网络问题返回
// 404 + {retryable:true}（前端据此决定"无音频"还是"重试"）。

import { resolveAudio } from "./_shared/audio.mjs";

export default async (req) => {
  const url = new URL(req.url);
  const word = (url.searchParams.get("word") || "").trim().toLowerCase();
  const accent = (url.searchParams.get("accent") || "").toLowerCase();
  if (!word || (accent !== "uk" && accent !== "us")) {
    return Response.json({ error: "word 与 accent=uk|us 必填" }, { status: 400 });
  }

  const result = await resolveAudio(word, accent);
  if (!result.ok) {
    return Response.json(
      { error: "没有可用的发音音频", accent, word, detail: result.error,
        retryable: Boolean(result.retryable) },
      { status: 404, headers: { "Access-Control-Allow-Origin": "*" } },
    );
  }
  return new Response(result.data, {
    headers: {
      "Content-Type": result.mime,
      "Cache-Control": "public, max-age=604800, immutable",
      "X-Audio-Provider": result.provider,
      "X-Audio-Duration": result.duration ? result.duration.toFixed(2) : "",
      "Access-Control-Allow-Origin": "*",
    },
  });
};

export const config = { path: "/api/audio" };
