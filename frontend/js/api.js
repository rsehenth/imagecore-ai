/* Camada de API: fetch JSON + upload com progresso (XHR) + envio binario.
   Padrao derivado de um projeto anterior do autor (frontend/js/api.js).
   Erros do servidor: `error` (mensagem tecnica em pt-BR, preservada para diagnostico) +
   `error_code` estavel e independente de idioma (Etapa 10.1) + `error_details` estruturado.
   O Error carrega .code e .details; a traducao fica em i18n.error() (fallback: mensagem bruta). */
(() => {
  const api = {
    async request(path, options = {}) {
      const isBlob = options.body instanceof Blob;
      const headers = { ...(options.headers || {}) };
      if (options.body && !isBlob && !headers['Content-Type']) headers['Content-Type'] = 'application/json';
      const response = await fetch(path, { ...options, headers });
      if (!response.ok) {
        let message = `${response.status} ${response.statusText}`;
        let code = null;
        let details = null;
        try {
          const data = await response.json();
          message = data.error || (typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail || data));
          code = data.error_code || null;
          details = data.error_details || null;
        } catch (err) { /* corpo sem JSON: usa o status */ }
        const error = new Error(message);
        error.status = response.status;
        error.code = code;
        error.details = details;
        throw error;
      }
      return response.status === 204 ? null : response.json();
    },
    get(path) { return this.request(path); },
    post(path, body) {
      const isRaw = body instanceof Blob;
      const payload = body === undefined ? '{}' : (isRaw ? body : JSON.stringify(body));
      return this.request(path, { method: 'POST', body: payload });
    },
    put(path, body) { return this.request(path, { method: 'PUT', body: JSON.stringify(body) }); },
    del(path) { return this.request(path, { method: 'DELETE' }); },
    uploadFile(path, file, onProgress) {
      return new Promise((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        xhr.open('POST', path);
        xhr.setRequestHeader('x-filename', encodeURIComponent(file.name || 'imagem.png'));
        xhr.setRequestHeader('Content-Type', 'application/octet-stream');
        xhr.upload.addEventListener('progress', (event) => {
          if (event.lengthComputable && onProgress) onProgress(Math.round((event.loaded / event.total) * 100));
        });
        xhr.addEventListener('load', () => {
          let data = null;
          try { data = JSON.parse(xhr.responseText); } catch (err) { /* resposta vazia */ }
          if (xhr.status >= 200 && xhr.status < 300) return resolve(data);
          const error = new Error((data && data.error) || i18n.t('api.uploadFailed', { status: xhr.status }));
          error.status = xhr.status;
          error.code = (data && data.error_code) || null;
          error.details = (data && data.error_details) || null;
          reject(error);
        });
        xhr.addEventListener('error', () => reject(new Error(i18n.t('api.uploadNetwork'))));
        xhr.send(file);
      });
    },
    imageUrl(genId, imageId) { return `/api/generations/${genId}/images/${imageId}/content`; },
    referenceUrl(refId) { return `/api/references/${refId}`; },
  };
  window.api = api;
})();
