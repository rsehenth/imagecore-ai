/* Testes das funcoes puras do editor de thumbnails (Etapa 6).
   Execucao: node tests/js/editor_core.test.mjs  (ou via pytest tests/test_editor_core_js.py) */
globalThis.window = globalThis;
await import('../../frontend/js/editor-core.js');

const assert = (await import('node:assert/strict')).default;
const { DEFAULT_TEXT_WIDTH, clamp, clamp01, normalizeLayerData, wrapLines } = globalThis.editorCore;

let passed = 0;
function check(name, fn) {
  fn();
  passed += 1;
  console.log(`ok - ${name}`);
}

/* medida de texto deterministica: cada caractere = 10 px */
function fakeCtx() {
  return { measureText: (value) => ({ width: String(value).length * 10 }) };
}

check('clamp e clamp01 respeitam limites', () => {
  assert.equal(clamp(5, 0, 3), 3);
  assert.equal(clamp(-1, 0, 3), 0);
  assert.equal(clamp01(1.5), 1);
  assert.equal(clamp01('nao-numero'), 0);
});

check('camada sem widthPct herda a largura padrao (compatibilidade Etapa 5)', () => {
  const layer = normalizeLayerData({});
  assert.equal(layer.widthPct, DEFAULT_TEXT_WIDTH);
  assert.equal(layer.widthPct, 0.92);
  assert.equal(layer.align, 'center');
  assert.equal(layer.font, "Impact, 'Arial Black', sans-serif");
});

check('widthPct por camada: valor valido preservado, fora da faixa saturado', () => {
  assert.equal(normalizeLayerData({ widthPct: 0.5 }).widthPct, 0.5);
  assert.equal(normalizeLayerData({ widthPct: 1.4 }).widthPct, 1);
  assert.equal(normalizeLayerData({ widthPct: -2 }).widthPct, 0.1);
  assert.equal(normalizeLayerData({ widthPct: 'x' }).widthPct, 0.92); // NaN cai no padrao
});

check('normalizacao de campos herdados (align/rotacao/tamanho)', () => {
  const layer = normalizeLayerData({ align: 'justificado', rotation: 999, sizePct: 999 });
  assert.equal(layer.align, 'center');
  assert.equal(layer.rotation, 180);
  assert.equal(layer.sizePct, 30);
});

check('wrapLines quebra por palavras dentro da largura da caixa', () => {
  const ctx = fakeCtx();
  // largura 50 px = 5 caracteres por linha
  assert.deepEqual(wrapLines(ctx, 'abc def ghi', 50, 4), ['abc', 'def', 'ghi']);
  assert.deepEqual(wrapLines(ctx, 'abc def', 50, 4), ['abc', 'def']);
});

check('wrapLines respeita o teto de linhas', () => {
  const ctx = fakeCtx();
  assert.deepEqual(wrapLines(ctx, 'a b c d e f', 10, 2), ['a', 'b']);
});

check('wrapLines mantem palavra unica longa (nao corta mid-word)', () => {
  const ctx = fakeCtx();
  assert.deepEqual(wrapLines(ctx, 'abcdefgh', 10, 4), ['abcdefgh']);
});

check('wrapLines com vazio devolve lista vazia', () => {
  assert.deepEqual(wrapLines(fakeCtx(), '   ', 100, 4), []);
});

console.log(`TODOS OK: ${passed} testes`);
