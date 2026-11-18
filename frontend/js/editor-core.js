/* Funcoes puras do editor de thumbnails (Etapa 6): sem DOM, testaveis em Node
   (tests/js/editor_core.test.mjs). Consumidas por editor.js. */
(() => {
  const DEFAULT_FONT = "Impact, 'Arial Black', sans-serif";
  /* Largura de composicao herdada da Etapa 5 (92% do canvas): valor padrao que mantem
     compatibilidade com projetos antigos sem widthPct salvo. */
  const DEFAULT_TEXT_WIDTH = 0.92;

  const clamp = (v, min, max) => Math.min(max, Math.max(min, v));
  const clamp01 = (v) => clamp(Number(v) || 0, 0, 1);
  /* Numero com fallback: valores corrompidos/nao numericos nunca viram NaN na cena. */
  const numOr = (value, fallback) => (Number.isFinite(Number(value)) ? Number(value) : fallback);

  function newId() {
    return (crypto.randomUUID ? crypto.randomUUID().replaceAll("-", "") : `${Date.now().toString(16)}${Math.random().toString(16).slice(2)}`).slice(0, 32);
  }

  function normalizeLayerData(raw) {
    const r = raw || {};
    return {
      text: typeof r.text === "string" ? r.text : "",
      font: r.font || DEFAULT_FONT,
      sizePct: clamp(numOr(r.sizePct, 10), 2, 30),
      color: r.color || "#ffffff",
      bold: Boolean(r.bold),
      align: r.align === "left" || r.align === "right" ? r.align : "center",
      rotation: clamp(numOr(r.rotation, 0), -180, 180),
      opacity: clamp(numOr(r.opacity, 1), 0, 1),
      x: clamp(numOr(r.x, 0.5), -0.05, 1.05),
      y: clamp(numOr(r.y, 0.5), -0.05, 1.05),
      widthPct: clamp(numOr(r.widthPct, DEFAULT_TEXT_WIDTH), 0.1, 1),
      outline: {
        on: Boolean(r.outline?.on),
        color: r.outline?.color || "#000000",
        widthPct: clamp(numOr(r.outline?.widthPct, 0.09), 0.02, 0.2),
      },
      shadow: { on: Boolean(r.shadow?.on), intensity: clamp(numOr(r.shadow?.intensity, 50), 0, 100) },
    };
  }

  /* Quebra de linha por palavras dentro da largura da caixa, com teto de linhas. */
  function wrapLines(ctx, value, maxWidth, maxLines) {
    const words = String(value || "").split(/\s+/).filter(Boolean);
    const lines = [];
    let line = "";
    for (const word of words) {
      const candidate = line ? `${line} ${word}` : word;
      if (ctx.measureText(candidate).width > maxWidth && line) { lines.push(line); line = word; } else line = candidate;
      if (lines.length === maxLines) break;
    }
    if (line && lines.length < maxLines) lines.push(line);
    return lines;
  }

  window.editorCore = { DEFAULT_FONT, DEFAULT_TEXT_WIDTH, clamp, clamp01, newId, normalizeLayerData, wrapLines };
})();
