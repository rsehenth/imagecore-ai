/* Verificacao do .gitignore (Etapa 10): simula as regras sobre arquivos-chave.
   Uso: node tests/js/gitignore_check.mjs  (apenas leitura; nao executa git). */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const rules = fs.readFileSync(path.join(root, '.gitignore'), 'utf8')
  .split(/\r?\n/)
  .map((l) => l.trim())
  .filter((l) => l && !l.startsWith('#'));

function ignored(rel) {
  let result = false;
  for (const rule of rules) {
    const neg = rule.startsWith('!');
    const pattern = neg ? rule.slice(1) : rule;
    const dirOnly = pattern.endsWith('/');
    const base = dirOnly ? pattern.slice(0, -1) : pattern;
    const glob = base.replace(/[.+^${}()|[\]\\]/g, '\\$&').replace(/\*/g, '[^/]*').replace(/\?/g, '.');
    const rx = new RegExp(`(^|/)${glob}${dirOnly ? '(/|$)' : '$'}`);
    if (rx.test(rel)) result = !neg;
  }
  return result;
}

const keep = [
  'backend/main.py', 'frontend/index.html', 'frontend/js/i18n.js',
  'frontend/js/locales/pt-BR.js', 'frontend/js/locales/en-US.js', 'frontend/css/styles.css',
  'config/workflows/t2i.sdxl.json', 'config/settings.example.yaml', 'pyproject.toml',
  'README.md', 'LICENSE', 'THIRD_PARTY_NOTICES.md', 'DESIGN.md', '.impeccable/design.json', 'docs/product.md',
  'docs/images/mesa-de-prova.png',
  'tests/js/i18n.test.mjs', 'start.bat', '.gitignore',
];
const drop = [
  'data/imagecore.db', 'data/imagecore.db-wal', 'data/backups/x.zip', 'data/generations/a.png',
  'data/_smoke/c.txt', '.venv/Scripts/python.exe', 'backend/__pycache__/main.cpython-312.pyc',
  '.pytest_cache/v/cache/lastfailed', 'imagecore_ai.egg-info/PKG-INFO', '.playwright-mcp/shot.png',
  'model.safetensors', 'weights.gguf', 'backend.log', '.env',
  'config/settings.yaml', 'config/comfy.local.txt', '.commandcode/taste/taste.md',
  'archive.sqlite3-shm', 'archive.sqlite3-wal', 'internal.env.tmp', 'secrets.yaml',
  'uploads/photo.png', 'exports/proof.jpg', 'docs/evidence/smoke.png', 'docs/status.md',
  'test-results/report.json', 'playwright-report/index.html', 'dist/setup.exe',
];
let fail = 0;
for (const f of keep) {
  const ig = ignored(f);
  if (ig) { console.log(`ERRO: versionavel seria ignorado: ${f}`); fail += 1; }
}
for (const f of drop) {
  const ig = ignored(f);
  if (!ig) { console.log(`ERRO: sensivel/gerado seria versionado: ${f}`); fail += 1; }
}
console.log(fail ? `${fail} erro(s) no .gitignore` : `OK: ${keep.length} versionaveis preservados e ${drop.length} sensiveis/gerados ignorados`);
process.exit(fail ? 1 : 0);
