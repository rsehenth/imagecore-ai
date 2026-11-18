/* Testes do envelope de erro da API (Etapa 10.1).
   Execucao: node tests/js/api.test.mjs
   Valida: request() carrega .code/.details de error_code/error_details (aditivo, contrato
   antigo preservado) e uploadFile() faz o mesmo; sem codigo conhecido, .code fica null. */
globalThis.window = globalThis;
globalThis.i18n = { t: (key, vars) => key };

let fetchResponse = null;
globalThis.fetch = async () => fetchResponse;
let xhrHandler = null;
class FakeXHR {
  open() {}
  setRequestHeader() {}
  upload = { addEventListener() {} };
  addEventListener(type, fn) { if (type === 'load') xhrHandler = fn; }
  send() {
    this.status = fetchResponse.xhrStatus;
    this.responseText = fetchResponse.body;
    xhrHandler.call(this);
  }
}
globalThis.XMLHttpRequest = FakeXHR;
await import('../../frontend/js/api.js');

import assert from 'node:assert/strict';

let passed = 0;
function check(name, fn) {
  fn();
  passed += 1;
  console.log(`ok - ${name}`);
}

const json = (status, body) => ({ ok: false, status, statusText: 'X', json: async () => body });

check('request() carrega error_code e error_details no Error', async () => {
  fetchResponse = json(409, { error: 'Perfil nao instalado.', error_code: 'profile_not_installed' });
  await assert.rejects(
    () => window.api.get('/api/x'),
    (err) => err.code === 'profile_not_installed' && err.details === null && err.status === 409
      && err.message === 'Perfil nao instalado.',
  );
});

check('request() com detalhes estruturados interpola depois (payload cru)', async () => {
  fetchResponse = json(400, {
    error: 'Steps fora da faixa.', error_code: 'invalid_steps', error_details: { min: 1, max: 8, received: 20 },
  });
  await assert.rejects(
    () => window.api.post('/api/x', {}),
    (err) => err.code === 'invalid_steps' && err.details.min === 1 && err.details.received === 20,
  );
});

check('request() sem codigo conhecido mantem .code null e a mensagem bruta (fallback)', async () => {
  fetchResponse = json(400, { error: 'Erro generico em pt-BR.' });
  await assert.rejects(
    () => window.api.get('/api/x'),
    (err) => err.code === null && err.message === 'Erro generico em pt-BR.',
  );
});

check('uploadFile() carrega error_code da resposta JSON', async () => {
  fetchResponse = { xhrStatus: 413, body: JSON.stringify({ error: 'Imagem acima do limite.', error_code: 'reference_too_large' }) };
  await assert.rejects(
    () => window.api.uploadFile('/api/references', new Blob([new Uint8Array(1)])),
    (err) => err.code === 'reference_too_large' && err.status === 413,
  );
});

console.log(`\n${passed} checks ok — envelope de erro da API consistente`);
