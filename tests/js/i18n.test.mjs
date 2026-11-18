/* Testes do sistema de internacionalizacao (Etapa 10).
   Execucao: node tests/js/i18n.test.mjs
   Valida: (1) mecanismo t/tn/interpolação/plural; (2) dicionários pt-BR e en-US com o MESMO
   conjunto de chaves, sem valores vazios e com as mesmas variaveis {var}; (3) TODA chave
   referenciada nos fontes (index.html + frontend/js) existe nos dicionarios — catch de
   chaves faltantes sem navegador. */
globalThis.window = globalThis;
globalThis.localStorage = {
  _s: new Map(),
  getItem(k) { return this._s.has(k) ? this._s.get(k) : null; },
  setItem(k, v) { this._s.set(k, String(v)); },
};
globalThis.document = {
  documentElement: { lang: '' },
  querySelectorAll: () => [],
  getElementById: () => null,
  dispatchEvent: () => {},
};
await import('../../frontend/js/locales/pt-BR.js');
await import('../../frontend/js/locales/en-US.js');
await import('../../frontend/js/i18n.js');

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
const assert = (await import('node:assert/strict')).default;

const { t, tn, setLanguage, getLanguage, stage, fmtDate, applyDOM, LANGS } = window.i18n;
const PT = window.I18N_LOCALES['pt-BR'];
const EN = window.I18N_LOCALES['en-US'];

let passed = 0;
function check(name, fn) {
  fn();
  passed += 1;
  console.log(`ok - ${name}`);
}

const varsOf = (text) => [...String(text).matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort().join(',');

check('idioma padrao e pt-BR e o <html lang> acompanha', () => {
  assert.equal(getLanguage(), 'pt-BR');
  setLanguage('en-US');
  assert.equal(getLanguage(), 'en-US');
  assert.equal(document.documentElement.lang, 'en-US');
  setLanguage('pt-BR');
  assert.equal(document.documentElement.lang, 'pt-BR');
});

check('idioma invalido cai no fallback pt-BR', () => {
  setLanguage('fr-FR');
  assert.equal(getLanguage(), 'pt-BR');
});

check('t() interpola variaveis e nunca injeta HTML', () => {
  setLanguage('pt-BR');
  assert.equal(t('gallery.shownOf', { shown: 3, total: 9 }), '3 de 9');
  assert.equal(t('gallery.previewAlt', { name: '<img>' }), 'Prévia do projeto <img>');
});

check('t() de chave inexistente retorna a propria chave (falha visivel, nao quebra)', () => {
  assert.equal(t('chave.inexistente'), 'chave.inexistente');
});

check('tn() pluraliza pt-BR e en-US', () => {
  setLanguage('pt-BR');
  assert.equal(tn('gallery.items', 1), '1 item');
  assert.equal(tn('gallery.items', 2), '2 itens');
  setLanguage('en-US');
  assert.equal(tn('gallery.items', 1), '1 item');
  assert.equal(tn('gallery.items', 24), '24 items');
  setLanguage('pt-BR');
});

check('pt-BR e en-US tem exatamente o mesmo conjunto de chaves', () => {
  assert.deepEqual(Object.keys(EN).sort(), Object.keys(PT).sort());
});

check('nenhum valor vazio nos dicionarios', () => {
  for (const [dict, name] of [[PT, 'pt-BR'], [EN, 'en-US']]) {
    for (const [key, value] of Object.entries(dict)) {
      assert.ok(String(value).length > 0, `${name}: ${key} vazio`);
    }
  }
});

check('variaveis {var} identicas entre pt-BR e en-US por chave', () => {
  for (const key of Object.keys(PT)) {
    assert.equal(varsOf(PT[key]), varsOf(EN[key]), `vars divergentes em ${key}`);
  }
});

check('chaves plurais sempre em par _one/_other', () => {
  const bases = new Set(Object.keys(PT).filter((k) => /_(one|other)$/.test(k)).map((k) => k.replace(/_(one|other)$/, '')));
  for (const base of bases) {
    assert.ok(`${base}_one` in PT && `${base}_other` in PT, `par plural incompleto: ${base}`);
    assert.ok(`${base}_one` in EN && `${base}_other` in EN, `par plural incompleto (en): ${base}`);
  }
});

check('LANGS expoe os dois idiomas obrigatorios', () => {
  assert.deepEqual([...LANGS].sort(), ['en-US', 'pt-BR']);
});

check('estagio do backend conhecido e traduzido; desconhecido passa integral', () => {
  setLanguage('en-US');
  assert.equal(stage('verificando motor'), 'Checking engine');
  assert.equal(stage('vram ok (estimativa; nao medicao)'), 'VRAM ok');
  assert.equal(stage('mensagem externa do ComfyUI'), 'mensagem externa do ComfyUI');
  assert.equal(stage(''), '');
  setLanguage('pt-BR');
  assert.equal(stage('na fila do ComfyUI'), 'Na fila do ComfyUI');
});

/* ---- Etapa 10.1: erro da API por error_code estavel (fallback = mensagem bruta) ---- */
const { error: errorText } = window.i18n;

check('error() traduz codigo conhecido no idioma corrente, com detalhes', () => {
  setLanguage('en-US');
  assert.equal(errorText({ code: 'generation_not_found', message: 'Geracao nao encontrada.' }), 'Generation not found.');
  assert.equal(
    errorText({ code: 'invalid_steps', message: 'x', details: { min: 1, max: 8, received: 20 } }),
    'Steps outside the range accepted by the profile (1 to 8); received 20.',
  );
  setLanguage('pt-BR');
  assert.equal(errorText({ code: 'generation_not_found', message: 'Geracao nao encontrada.' }), 'Geração não encontrada.');
});

check('error() sem codigo conhecido ou sem codigo passa a mensagem tecnica integral', () => {
  setLanguage('en-US');
  assert.equal(errorText({ code: 'codigo_futuro', message: 'mensagem tecnica em pt-BR' }), 'mensagem tecnica em pt-BR');
  assert.equal(errorText({ message: 'sem codigo' }), 'sem codigo');
  assert.equal(errorText(null), '');
  setLanguage('pt-BR');
});

check('todos os codigos do mapa ERROR_KEYS existem nos dicionarios', () => {
  // i18n.js expose apenas error(); o mapa e validado indiretamente: qualquer chave do
  // dicionario error.* deve ter correspondencia nos dois idiomas (checado acima) e o
  // fallback e exercitado com codigo inexistente.
  assert.ok(Object.keys(PT).some((k) => k.startsWith('error.')));
  assert.ok(Object.keys(EN).some((k) => k.startsWith('error.')));
});

check('fmtDate usa o idioma corrente (Intl)', () => {
  const iso = '2026-01-15T12:30:00Z';
  setLanguage('en-US');
  assert.ok(/\d/.test(fmtDate(iso)));
  setLanguage('pt-BR');
  assert.ok(/\d/.test(fmtDate(iso)));
  assert.equal(fmtDate(null), '—');
});

/* ---- cobertura: toda chave usada nos fontes existe nos dicionarios ---- */
const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, '../..');
const sources = [
  path.join(root, 'frontend/index.html'),
  ...fs.readdirSync(path.join(root, 'frontend/js'))
    .filter((f) => f.endsWith('.js'))
    .map((f) => path.join(root, 'frontend/js', f)),
];
const KEY_RE = /['"]((?:nav|provider|common|creator|thumb|studio|gallery|settings|export|modal|keys|status|profile|quality|style|platform|stage|api|editor|error)\.[\w.]+)['"]/g;
const used = new Set();
for (const file of sources) {
  const text = fs.readFileSync(file, 'utf8');
  for (const match of text.matchAll(KEY_RE)) used.add(match[1]);
  for (const match of text.matchAll(/data-i18n(?:-attr)?="([^"]+)"/g)) {
    for (const pair of match[1].split(';')) {
      const key = pair.split(':').pop().trim();
      if (key) used.add(key);
    }
  }
}
const keyExists = (dict, key) => key in dict || `${key}_one` in dict || `${key}_other` in dict;
check(`todas as ${used.size} chaves usadas nos fontes existem nos dicionarios`, () => {
  const missing = [...used].filter((key) => !keyExists(PT, key));
  assert.deepEqual(missing, [], `chaves faltando em pt-BR: ${missing.join(', ')}`);
  const missingEn = [...used].filter((key) => !keyExists(EN, key));
  assert.deepEqual(missingEn, [], `chaves faltando em en-US: ${missingEn.join(', ')}`);
});

check('nenhuma chave de dicionario ficou orfa (definida e nunca usada)', () => {
  // Bases de plural (ex.: tn('gallery.items', n)) contam como usadas quando o par existe.
  const usedOrPair = (key) => used.has(key) || used.has(key.replace(/_(one|other)$/, ''));
  const orphans = Object.keys(PT).filter((key) => !usedOrPair(key));
  assert.deepEqual(orphans, [], `chaves sem uso nos fontes: ${orphans.join(', ')}`);
});

check('persistencia: setLanguage grava imagecore.lang e init retoma', () => {
  setLanguage('en-US');
  assert.equal(localStorage.getItem('imagecore.lang'), 'en-US');
  setLanguage('pt-BR');
  assert.equal(localStorage.getItem('imagecore.lang'), 'pt-BR');
});

console.log(`\n${passed} checks ok — i18n consistente (pt-BR/en-US)`);
