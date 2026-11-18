/* Editor visual de thumbnails (Etapa 6): camadas de texto com CAIXA redimensionavel por
   camada (largura individual + quebra de linha dentro dos limites), tipografia, composicao
   (guias/encaixe/zoom), fundo IA ou local, undo/redo (restaura selecao), indicacao de
   alteracoes nao salvas, preview reduzido e renderizacao WYSIWYG compartilhada entre
   preview e exportacao.
   Uma instancia por studio de thumbnail (prefixo t-). Funcoes puras em editor-core.js. */
(() => {
  const { clamp, clamp01, newId, normalizeLayerData, wrapLines } = window.editorCore;
  const MAX_LAYERS = 5;

  const FONTS = [
    { v: "Impact, 'Arial Black', sans-serif", l: "Impact" },
    { v: "'Arial Black', sans-serif", l: "Arial Black" },
    { v: "'Bahnschrift SemiBold', Bahnschrift, 'Segoe UI', sans-serif", l: "Bahnschrift" },
    { v: "'Franklin Gothic Medium', 'Arial Narrow', Arial, sans-serif", l: "Franklin Gothic" },
    { v: "'Segoe UI', Arial, sans-serif", l: "Segoe UI" },
    { v: "'Trebuchet MS', sans-serif", l: "Trebuchet MS" },
    { v: "Verdana, Geneva, sans-serif", l: "Verdana" },
    { v: "Georgia, 'Times New Roman', serif", l: "Georgia" },
    { v: "Consolas, 'Courier New', monospace", l: "Mono" },
  ];

  /* Estilos de texto predefinidos (editaveis depois de aplicados: viram camadas comuns). */
  const STYLE_PRESETS = {
    impacto: [
      { text: "TÍTULO IMPACTO", font: FONTS[0].v, sizePct: 16, color: "#ffffff", bold: true, align: "center", rotation: 0, opacity: 1, x: 0.5, y: 0.76, outline: { on: true, color: "#000000", widthPct: 0.1 }, shadow: { on: true, intensity: 55 } },
    ],
    tecnologia: [
      { text: "TECNOLOGIA", font: FONTS[2].v, sizePct: 10, color: "#1ab6dc", bold: true, align: "center", rotation: 0, opacity: 1, x: 0.5, y: 0.86, outline: { on: true, color: "#04222c", widthPct: 0.05 }, shadow: { on: true, intensity: 70 } },
    ],
    minimalista: [
      { text: "título discreto", font: FONTS[4].v, sizePct: 6.5, color: "#ffffff", bold: false, align: "center", rotation: 0, opacity: 0.96, x: 0.5, y: 0.9, outline: { on: false, color: "#000000", widthPct: 0.05 }, shadow: { on: true, intensity: 30 } },
    ],
    contraste: [
      { text: "TÍTULO", font: FONTS[0].v, sizePct: 13, color: "#ffffff", bold: true, align: "center", rotation: 0, opacity: 1, x: 0.5, y: 0.64, outline: { on: true, color: "#101418", widthPct: 0.09 }, shadow: { on: true, intensity: 45 } },
      { text: "DESTAQUE", font: FONTS[0].v, sizePct: 9, color: "#ffd400", bold: true, align: "center", rotation: 0, opacity: 1, x: 0.5, y: 0.84, outline: { on: true, color: "#101418", widthPct: 0.09 }, shadow: { on: true, intensity: 45 } },
    ],
  };

  const ICON = (name) => `<svg class="ic" aria-hidden="true"><use href="#i-${name}"/></svg>`;

  /* Barra de ferramentas (acima do canvas) e inspetor (camadas + propriedades) vivem em
     dois hosts: o layout do estudio decide onde cada um aparece. Textos via data-i18n
     (aplicados por i18n.applyDOM apos o innerHTML — traducao nunca injeta HTML). */
  const TOOLBAR_TEMPLATE = `
    <div class="ed-toolbar" role="toolbar" aria-label="Ferramentas do canvas" data-i18n-attr="aria-label:editor.toolbarAria">
      <div class="ed-group">
        <button type="button" class="btn xs" data-ed="undo" data-i18n-attr="title:editor.undoTitle">${ICON("undo")}<span data-i18n="editor.undo">Desfazer</span></button>
        <button type="button" class="btn xs" data-ed="redo" data-i18n-attr="title:editor.redoTitle">${ICON("redo")}<span data-i18n="editor.redo">Refazer</span></button>
      </div>
      <div class="ed-group" role="group" data-i18n-attr="aria-label:editor.zoomGroupAria">
        <button type="button" class="btn xs" data-ed="zoom-out" data-i18n-attr="aria-label:editor.zoomOutAria;title:editor.zoomOutTitle">${ICON("minus")}</button>
        <span class="ed-zoomval mono" data-ed="zoomval" data-i18n-attr="title:editor.zoomValTitle">—</span>
        <button type="button" class="btn xs" data-ed="zoom-in" data-i18n-attr="aria-label:editor.zoomInAria;title:editor.zoomInTitle">${ICON("plus")}</button>
        <button type="button" class="btn xs" data-ed="zoom-fit" data-i18n-attr="title:editor.zoomFitTitle"><span data-i18n="editor.zoomFit">Ajustar</span></button>
      </div>
      <div class="ed-group">
        <label class="tc-check" data-i18n-attr="title:editor.guidesTitle"><input type="checkbox" data-ed="guides" checked><span data-i18n="editor.guides">Guias</span></label>
        <label class="tc-check" data-i18n-attr="title:editor.snapTitle"><input type="checkbox" data-ed="snap" checked><span data-i18n="editor.snap">Encaixe</span></label>
      </div>
      <div class="ed-group">
        <label class="ed-inline"><span data-i18n="editor.background">Fundo</span>
          <select data-ed="bgfit" data-i18n-attr="aria-label:editor.bgFitAria">
            <option value="cover" data-i18n="editor.bgCover">Cobrir</option>
            <option value="contain" data-i18n="editor.bgContain">Ajustar</option>
          </select>
        </label>
        <button type="button" class="btn xs" data-ed="bgreset" data-i18n-attr="title:editor.bgResetTitle"><span data-i18n="editor.bgReset">Recentralizar</span></button>
      </div>
      <div class="ed-group ed-savegroup">
        <input type="text" class="ed-name" data-ed="name" maxlength="120" data-i18n-attr="placeholder:editor.namePlaceholder;aria-label:editor.nameAria" placeholder="Nome do projeto" aria-label="Nome do projeto">
        <button type="button" class="btn xs primary" data-ed="save" data-i18n-attr="title:editor.saveTitle">${ICON("save")}<span data-ed="savelabel" data-i18n="editor.save">Salvar projeto</span></button>
      </div>
    </div>`;

  const PANELS_TEMPLATE = `
    <section class="ed-panel ed-mini" data-ed="miniwrap">
      <div class="ed-panel-head"><h4 data-i18n="editor.miniTitle">Prévia reduzida</h4></div>
      <canvas data-ed="mini" data-i18n-attr="aria-label:editor.miniAria" aria-label="Prévia reduzida da composição"></canvas>
      <p class="hint" data-i18n="editor.miniHint">Confira se o título continua legível em tamanho pequeno.</p>
    </section>
    <section class="ed-panel">
      <header class="ed-panel-head">
        <h4><span data-i18n="editor.layers">Camadas</span> <span class="ed-count mono" data-ed="count">0/5</span></h4>
        <button type="button" class="btn xs primary" data-ed="add">${ICON("plus")}<span data-i18n="editor.addText">Texto</span></button>
      </header>
      <ul class="ed-layers" data-ed="layerlist" data-i18n-attr="aria-label:editor.layersAria;data-empty:editor.layersEmpty" aria-label="Camadas de texto" data-empty="Nenhuma camada de texto ainda. Use “+ Texto” ou um estilo pronto."></ul>
      <span class="sr-only" role="status" data-ed="layersempty"></span>
      <div class="ed-presets">
        <span class="ed-label" data-i18n="editor.presetLabel">Estilos prontos (editáveis depois)</span>
        <div class="ed-presetrow">
          <button type="button" class="btn xs" data-preset="impacto" data-i18n="editor.presetImpacto">Impacto</button>
          <button type="button" class="btn xs" data-preset="tecnologia" data-i18n="editor.presetTecnologia">Tecnologia</button>
          <button type="button" class="btn xs" data-preset="minimalista" data-i18n="editor.presetMinimalista">Minimalista</button>
          <button type="button" class="btn xs" data-preset="contraste" data-i18n="editor.presetContraste">Contraste</button>
        </div>
      </div>
    </section>
    <section class="ed-panel ed-typo" data-ed="typopanel">
      <header class="ed-panel-head">
        <h4 data-i18n="editor.layerPanelTitle">Texto da camada</h4>
        <span class="ed-layerdims mono" data-ed="layerdims">—</span>
      </header>
      <p class="ed-empty" data-i18n="editor.layerPanelEmpty">Selecione uma camada na lista ou clique no texto sobre o canvas para editar.</p>
      <textarea data-ed="text" rows="2" maxlength="300" data-i18n-attr="placeholder:editor.layerTextPlaceholder;aria-label:editor.layerTextAria" placeholder="Conteúdo da camada de texto" aria-label="Conteúdo da camada de texto"></textarea>
      <div class="ed-legend"><span><i></i><span data-i18n="editor.legendBox">Quadrado: largura da caixa</span></span><span><i class="circ"></i><span data-i18n="editor.legendCircle">Círculo: tamanho da fonte</span></span></div>
      <div class="ed-grid">
        <label class="ed-field ed-field-wide"><span data-i18n="editor.font">Fonte</span> <select data-ed="font"></select></label>
        <label class="ed-field ed-field-wide"><span data-i18n="editor.size">Tamanho</span>
          <span class="ed-rangerow"><input type="range" data-ed="size" min="2" max="30" step="0.5" data-i18n-attr="aria-label:editor.sizeAria"><span class="mono" data-ed="sizeval">—</span></span>
        </label>
        <label class="ed-field"><span data-i18n="editor.color">Cor</span> <input type="color" data-ed="color" data-i18n-attr="aria-label:editor.colorAria"></label>
        <label class="ed-field ed-checkfield"><span data-i18n="editor.bold">Negrito</span> <input type="checkbox" data-ed="bold"></label>
        <div class="ed-field ed-field-wide" role="group" data-i18n-attr="aria-label:editor.alignAria"><span data-i18n="editor.alignLabel">Alinhamento</span>
          <span class="ed-seg">
            <button type="button" data-ed="align" data-value="left" data-i18n-attr="title:editor.alignLeftTitle;aria-label:editor.alignLeftAria">${ICON("align-l")}</button>
            <button type="button" data-ed="align" data-value="center" data-i18n-attr="title:editor.alignCenterTitle;aria-label:editor.alignCenterAria">${ICON("align-c")}</button>
            <button type="button" data-ed="align" data-value="right" data-i18n-attr="title:editor.alignRightTitle;aria-label:editor.alignRightAria">${ICON("align-r")}</button>
          </span>
        </div>
        <label class="ed-field ed-field-wide"><span data-i18n="editor.opacity">Opacidade</span>
          <span class="ed-rangerow"><input type="range" data-ed="opacity" min="5" max="100" data-i18n-attr="aria-label:editor.opacityAria"><span class="mono" data-ed="opacityval">—</span></span>
        </label>
        <label class="ed-field ed-field-wide"><span data-i18n="editor.boxWidth">Largura da caixa</span>
          <span class="ed-rangerow"><input type="range" data-ed="width" min="10" max="100" data-i18n-attr="aria-label:editor.boxWidthAria"><span class="mono" data-ed="widthval">—</span></span>
        </label>
        <label class="ed-field"><span data-i18n="editor.rotation">Rotação (°)</span> <input type="number" data-ed="rotation" min="-180" max="180" step="1" data-i18n-attr="aria-label:editor.rotationAria"></label>
        <div class="ed-field" role="group" data-i18n-attr="aria-label:editor.onScreen"><span data-i18n="editor.onScreen">Centralizar no canvas</span>
          <span class="ed-seg">
            <button type="button" class="btn xs" data-ed="centerh" data-i18n-attr="title:editor.centerHTitle"><span data-i18n="editor.centerH">Horizontal</span></button>
            <button type="button" class="btn xs" data-ed="centerv" data-i18n-attr="title:editor.centerVTitle"><span data-i18n="editor.centerV">Vertical</span></button>
          </span>
        </div>
      </div>
      <div class="ed-sub" data-i18n="editor.outline">Contorno</div>
      <div class="ed-grid">
        <label class="ed-field ed-checkfield"><span data-i18n="editor.enable">Ativar</span> <input type="checkbox" data-ed="outline-on"></label>
        <label class="ed-field"><span data-i18n="editor.color">Cor</span> <input type="color" data-ed="outline-color" data-i18n-attr="aria-label:editor.outlineColorAria"></label>
        <label class="ed-field ed-field-wide"><span data-i18n="editor.thickness">Espessura</span>
          <span class="ed-rangerow"><input type="range" data-ed="outline-width" min="2" max="20" data-i18n-attr="aria-label:editor.thicknessAria"><span class="mono" data-ed="outlinewidthval">—</span></span>
        </label>
      </div>
      <div class="ed-sub" data-i18n="editor.shadow">Sombra</div>
      <div class="ed-grid">
        <label class="ed-field ed-checkfield"><span data-i18n="editor.enable">Ativar</span> <input type="checkbox" data-ed="shadow-on"></label>
        <label class="ed-field ed-field-wide"><span data-i18n="editor.intensity">Intensidade</span>
          <span class="ed-rangerow"><input type="range" data-ed="shadow-intensity" min="0" max="100" data-i18n-attr="aria-label:editor.intensityAria"><span class="mono" data-ed="shadowintensityval">—</span></span>
        </label>
      </div>
    </section>`;

  function createEditor(options) {
    const opts = options;
    const root = opts.root;
    const canvas = opts.canvas;
    const stage = opts.stage;
    const empty = opts.empty;
    const toolbarHost = opts.toolbar;
    toolbarHost.innerHTML = TOOLBAR_TEMPLATE;
    root.innerHTML = PANELS_TEMPLATE;
    /* Traducao dos templates injetados (tambem reexecutada a cada troca de idioma). */
    i18n.applyDOM(toolbarHost);
    i18n.applyDOM(root);
    const $ = (name) => root.querySelector(`[data-ed="${name}"]`) || toolbarHost.querySelector(`[data-ed="${name}"]`);
    const els = {
      zoomOut: $("zoom-out"), zoomIn: $("zoom-in"), zoomFit: $("zoom-fit"), zoomVal: $("zoomval"),
      guides: $("guides"), snap: $("snap"), bgFit: $("bgfit"), bgReset: $("bgreset"),
      undo: $("undo"), redo: $("redo"), name: $("name"), save: $("save"), saveLabel: $("savelabel"),
      miniWrap: $("miniwrap"), mini: $("mini"),
      add: $("add"), layerList: $("layerlist"), count: $("count"), layersEmpty: $("layersempty"),
      typoPanel: $("typopanel"), layerDims: $("layerdims"),
      text: $("text"), font: $("font"), size: $("size"), sizeVal: $("sizeval"),
      color: $("color"), bold: $("bold"), opacity: $("opacity"), opacityVal: $("opacityval"),
      rotation: $("rotation"), width: $("width"), widthVal: $("widthval"),
      centerH: $("centerh"), centerV: $("centerv"),
      outlineOn: $("outline-on"), outlineColor: $("outline-color"), outlineWidth: $("outline-width"),
      outlineWidthVal: $("outlinewidthval"), shadowOn: $("shadow-on"), shadowIntensity: $("shadow-intensity"),
      shadowIntensityVal: $("shadowintensityval"),
    };
    for (const option of FONTS) {
      const el = document.createElement("option");
      el.value = option.v;
      el.textContent = option.l;
      els.font.appendChild(el);
    }

    const state = {
      active: false, layers: [], selectedId: null,
      bg: null, bgOffset: { x: 0, y: 0 },
      zoom: null, // null = ajustar (fit)
      history: [], histIndex: -1,
      project: { id: null, name: "" },
      drag: null,
    };

    const dims = () => opts.dims() || [1280, 720];
    const selected = () => state.layers.find((layer) => layer.id === state.selectedId) || null;
    const displayScale = () => {
      const rect = canvas.getBoundingClientRect();
      return rect.width ? rect.width / dims()[0] : 1;
    };

    /* ------------------------------------------------------------ modelo de camadas */
    function normalizeLayer(raw, id) {
      const layer = normalizeLayerData(raw);
      layer.id = id || (raw.id && typeof raw.id === "string" ? raw.id.slice(0, 32) : newId());
      layer._bounds = null;
      return layer;
    }
    const layerForSave = (layer) => {
      const { _bounds, ...clean } = layer;
      return clean;
    };

    /* ------------------------------------------------------------ historico (undo/redo) */
    function snapshot() {
      return JSON.stringify({
        layers: state.layers.map(layerForSave),
        fit: els.bgFit.value,
        offset: { ...state.bgOffset },
        selectedId: state.selectedId,
      });
    }
    function restore(serialized) {
      const data = JSON.parse(serialized);
      state.layers = data.layers.map((layer) => normalizeLayer(layer));
      els.bgFit.value = data.fit === "contain" ? "contain" : "cover";
      state.bgOffset = data.offset || { x: 0, y: 0 };
      /* Etapa 6: a selecao acompanha o undo/redo (camada volta selecionada). */
      state.selectedId = data.layers.some((layer) => layer.id === data.selectedId) ? data.selectedId : null;
      refreshLayersUI();
      refreshTypoUI();
      scheduleRender();
    }
    function resetHistory() {
      state.history = [snapshot()];
      state.histIndex = 0;
      updateUndoButtons();
      markClean();
    }
    function pushHistory() {
      const current = snapshot();
      if (state.history[state.histIndex] === current) return;
      state.history = state.history.slice(0, state.histIndex + 1);
      state.history.push(current);
      if (state.history.length > 60) state.history.shift();
      state.histIndex = state.history.length - 1;
      updateUndoButtons();
      notifyDirty();
    }
    function undo() {
      if (state.histIndex <= 0) return;
      state.histIndex -= 1;
      restore(state.history[state.histIndex]);
      updateUndoButtons();
      notifyDirty();
    }
    function redo() {
      if (state.histIndex >= state.history.length - 1) return;
      state.histIndex += 1;
      restore(state.history[state.histIndex]);
      updateUndoButtons();
      notifyDirty();
    }
    function updateUndoButtons() {
      els.undo.disabled = state.histIndex <= 0;
      els.redo.disabled = state.histIndex >= state.history.length - 1;
    }

    /* ------------------------------------------------------------ alteracoes nao salvas */
    let clean = null; // { snap, name } na base do ultimo salvamento/carregamento
    function isDirty() {
      if (!state.active || !clean) return false;
      return snapshot() !== clean.snap || (els.name.value.trim() || "") !== clean.name;
    }
    function markClean() {
      clean = { snap: snapshot(), name: els.name.value.trim() || "" };
      notifyDirty();
    }
    function notifyDirty() {
      const dirty = isDirty();
      els.saveLabel.textContent = dirty ? i18n.t("editor.saveDirty") : i18n.t("editor.save");
      els.save.title = dirty ? i18n.t("editor.saveDirtyTitle") : i18n.t("editor.saveCleanTitle");
      if (opts.onDirtyChange) opts.onDirtyChange(dirty);
    }

    /* ------------------------------------------------------------ renderizacao WYSIWYG */
    function drawBackground(ctx, W, H) {
      ctx.fillStyle = "#dcdde1";
      ctx.fillRect(0, 0, W, H);
      const bg = state.bg;
      if (!bg || !bg.img.complete || !bg.img.naturalWidth) return;
      const iw = bg.img.naturalWidth, ih = bg.img.naturalHeight;
      const scale = els.bgFit.value === "contain" ? Math.min(W / iw, H / ih) : Math.max(W / iw, H / ih);
      const dw = iw * scale, dh = ih * scale;
      const extraX = Math.max(0, dw - W), extraY = Math.max(0, dh - H);
      const dx = (W - dw) / 2 + state.bgOffset.x * extraX;
      const dy = (H - dh) / 2 + state.bgOffset.y * extraY;
      ctx.imageSmoothingQuality = "high";
      ctx.drawImage(bg.img, dx, dy, dw, dh);
    }
    function bgExtra() {
      const bg = state.bg;
      if (!bg || !bg.img.complete) return { x: 0, y: 0 };
      const [W, H] = dims();
      const iw = bg.img.naturalWidth, ih = bg.img.naturalHeight;
      const scale = Math.max(W / iw, H / ih); // pan so no modo cobrir
      return { x: Math.max(0, iw * scale - W), y: Math.max(0, ih * scale - H) };
    }

    function drawTextLayer(ctx, W, H, layer) {
      const value = (layer.text || "").trim();
      if (!value) { layer._bounds = null; return; }
      const fontPx = Math.max(6, (H * Number(layer.sizePct || 10)) / 100);
      ctx.save();
      ctx.globalAlpha = clamp01(layer.opacity);
      ctx.font = `${layer.bold ? "700" : "400"} ${fontPx}px ${layer.font}`;
      ctx.textBaseline = "middle";
      /* Etapa 6: caixa de texto com largura individual (widthPct); a quebra de linha
         respeita o limite da caixa, nao mais os 92% fixos do canvas. */
      const boxW = W * clamp(Number(layer.widthPct), 0.1, 1);
      const lines = wrapLines(ctx, value, boxW, 4);
      const lineH = fontPx * 1.15;
      const totalH = lines.length * lineH;
      const cx = layer.x * W, cy = layer.y * H;
      const rotDeg = Number(layer.rotation || 0);
      ctx.translate(cx, cy);
      if (rotDeg) ctx.rotate((rotDeg * Math.PI) / 180);
      const align = layer.align;
      ctx.textAlign = align;
      /* Alinhamento ancorado nos LIMITES da caixa (preservado ao redimensionar). */
      const anchorX = align === "left" ? -boxW / 2 : align === "right" ? boxW / 2 : 0;
      const outlineOn = Boolean(layer.outline?.on);
      const outlineW = outlineOn ? Math.max(1.5, fontPx * Number(layer.outline?.widthPct ?? 0.09)) : 0;
      const shadowOn = Boolean(layer.shadow?.on);
      const intensity = clamp01((Number(layer.shadow?.intensity ?? 50) || 0) / 100);
      for (const [index, line] of lines.entries()) {
        const y = -totalH / 2 + lineH * (index + 0.5);
        if (outlineOn) {
          ctx.shadowColor = "transparent";
          ctx.lineWidth = outlineW;
          ctx.strokeStyle = layer.outline?.color || "#000000";
          ctx.lineJoin = "round";
          ctx.miterLimit = 2;
          ctx.strokeText(line, anchorX, y);
        }
        if (shadowOn) {
          ctx.shadowColor = `rgba(0, 0, 0, ${(0.3 + intensity * 0.55).toFixed(3)})`;
          ctx.shadowBlur = fontPx * (0.04 + intensity * 0.22);
          ctx.shadowOffsetY = fontPx * (0.02 + intensity * 0.07);
        } else {
          ctx.shadowColor = "transparent";
          ctx.shadowBlur = 0;
          ctx.shadowOffsetY = 0;
        }
        ctx.fillStyle = layer.color || "#ffffff";
        ctx.fillText(line, anchorX, y);
      }
      ctx.restore();
      /* Bounds normalizados (0..1 do canvas): GEOMETRIA DA CAIXA (nao do texto medido),
         para preview, mini-preview e hit-test compartilharem a mesma geometria. */
      layer._bounds = { cx: cx / W, cy: cy / H, w: boxW / W, h: totalH / H, rot: rotDeg };
    }

    function drawOverlay(ctx, W, H, ds) {
      const hairline = Math.max(1, 1.5 / ds);
      if (els.guides.checked) {
        const inset = 0.05;
        ctx.save();
        ctx.strokeStyle = "rgba(255, 255, 255, .85)";
        ctx.shadowColor = "rgba(23, 25, 43, .55)";
        ctx.shadowBlur = 2;
        ctx.setLineDash([8, 6]);
        ctx.lineWidth = hairline;
        ctx.strokeRect(W * inset, H * inset, W * (1 - 2 * inset), H * (1 - 2 * inset));
        ctx.restore();
      }
      const guides = state.drag && state.drag.guides;
      if (guides) {
        ctx.save();
        ctx.strokeStyle = "rgba(77, 107, 255, .98)";
        ctx.lineWidth = hairline;
        ctx.beginPath();
        if (guides.v != null) { ctx.moveTo(guides.v, 0); ctx.lineTo(guides.v, H); }
        if (guides.h != null) { ctx.moveTo(0, guides.h); ctx.lineTo(W, guides.h); }
        ctx.stroke();
        ctx.restore();
      }
      const layer = selected();
      if (layer && layer._bounds && (layer.text || "").trim()) {
        const b = layer._bounds;
        const bw = b.w * W, bh = b.h * H, bcx = b.cx * W, bcy = b.cy * H;
        ctx.save();
        ctx.translate(bcx, bcy);
        ctx.rotate(((b.rot || 0) * Math.PI) / 180);
        ctx.strokeStyle = "#ff4f9a";
        ctx.shadowColor = "rgba(23, 25, 43, .5)";
        ctx.shadowBlur = 2;
        ctx.lineWidth = hairline * 1.2;
        ctx.setLineDash([7, 5]);
        ctx.strokeRect(-bw / 2, -bh / 2, bw, bh);
        ctx.setLineDash([]);
        ctx.shadowBlur = 0;
        const hs = 9 / ds;
        ctx.strokeStyle = "#ffffff";
        ctx.lineWidth = Math.max(1.5, 2 / ds);
        /* Alca inferior-direita: redimensiona a LARGURA DA CAIXA (Etapa 6). */
        ctx.fillStyle = "#ff4f9a";
        ctx.fillRect(bw / 2 - hs, bh / 2 - hs, hs * 2, hs * 2);
        ctx.strokeRect(bw / 2 - hs, bh / 2 - hs, hs * 2, hs * 2);
        /* Alca superior-direita: escala o tamanho da fonte (comportamento da Etapa 5). */
        ctx.fillStyle = "#4d6bff";
        ctx.beginPath();
        ctx.arc(bw / 2, -bh / 2, hs, 0, Math.PI * 2);
        ctx.fill();
        ctx.stroke();
        ctx.restore();
      }
    }

    function renderScene(ctx, W, H, { overlay = false, ds = 1 } = {}) {
      drawBackground(ctx, W, H);
      for (const layer of state.layers) drawTextLayer(ctx, W, H, layer);
      if (overlay) drawOverlay(ctx, W, H, ds);
    }

    let renderQueued = false;
    function scheduleRender() {
      if (renderQueued) return;
      renderQueued = true;
      requestAnimationFrame(() => { renderQueued = false; renderPreview(); });
    }

    function renderPreview() {
      if (!state.active) return;
      const [W, H] = dims();
      if (canvas.width !== W || canvas.height !== H) { canvas.width = W; canvas.height = H; }
      const scale = state.zoom == null ? fitScale() : state.zoom;
      canvas.style.width = `${Math.round(W * scale)}px`;
      canvas.style.height = `${Math.round(H * scale)}px`;
      stage.classList.toggle("ed-zoomed", state.zoom != null && W * scale > stage.clientWidth - 76);
      els.zoomVal.textContent = `${Math.round(scale * 100)}%`;
      const ctx = canvas.getContext("2d");
      renderScene(ctx, W, H, { overlay: true, ds: scale });
      renderMini();
      const sel = selected();
      els.layerDims.textContent = sel && sel._bounds
        ? `${Math.round(sel._bounds.w * W)} × ${Math.round(sel._bounds.h * H)} px`
        : "—";
    }
    function fitScale() {
      const [W, H] = dims();
      const availW = Math.max(120, stage.clientWidth - 76);
      const availH = Math.max(120, stage.clientHeight - 76);
      return Math.min(availW / W, availH / H);
    }
    function renderMini() {
      const [W, H] = dims();
      const miniW = 168;
      const miniH = Math.max(40, Math.round((miniW * H) / W));
      if (els.mini.width !== miniW || els.mini.height !== miniH) { els.mini.width = miniW; els.mini.height = miniH; }
      const ctx = els.mini.getContext("2d");
      renderScene(ctx, miniW, miniH);
    }

    /* ------------------------------------------------------------ UI: lista e tipografia */
    function layerName(layer) {
      const text = (layer.text || "").trim();
      return text ? (text.length > 22 ? `${text.slice(0, 22)}…` : text) : i18n.t("editor.emptyLayer");
    }
    function refreshLayersUI() {
      els.layerList.textContent = "";
      els.count.textContent = `${state.layers.length}/${MAX_LAYERS}`;
      /* Alternativa acessivel ao ::before do CSS (:empty + attr(data-empty)). */
      els.layersEmpty.textContent = state.layers.length ? "" : i18n.t("editor.layersEmpty");
      for (let index = state.layers.length - 1; index >= 0; index--) {
        const layer = state.layers[index];
        const li = document.createElement("li");
        li.className = "ed-layer" + (layer.id === state.selectedId ? " selected" : "");
        li.dataset.id = layer.id;
        /* Etapa 6: item focavel — Enter/Espaco seleciona (navegacao por teclado). */
        li.tabIndex = 0;
        li.setAttribute("role", "button");
        li.setAttribute("aria-label", i18n.t("editor.layerAria", { name: layerName(layer) }));
        li.addEventListener("keydown", (event) => {
          if (event.key === "Enter" || event.key === " ") { event.preventDefault(); select(layer.id); }
        });
        const name = document.createElement("span");
        name.className = "ed-layer-name";
        name.textContent = layerName(layer);
        name.title = layer.text || i18n.t("editor.emptyLayer");
        const actions = document.createElement("span");
        actions.className = "ed-layer-actions";
        const mk = (iconName, act, actionKey) => {
          const btn = document.createElement("button");
          btn.type = "button";
          btn.innerHTML = ICON(iconName);
          const title = i18n.t(actionKey);
          btn.title = title;
          btn.setAttribute("aria-label", i18n.t("editor.layerActionAria", { action: title, name: name.textContent }));
          btn.addEventListener("click", (event) => { event.stopPropagation(); layerAction(layer.id, act); });
          return btn;
        };
        actions.append(
          mk("up", "up", "editor.layerUp"),
          mk("down", "down", "editor.layerDown"),
          mk("copy", "dup", "editor.layerDup"),
          mk("trash", "del", "editor.layerDel"),
        );
        li.addEventListener("click", () => { select(layer.id); });
        li.append(name, actions);
        els.layerList.appendChild(li);
      }
    }
    function layerAction(id, act) {
      const index = state.layers.findIndex((layer) => layer.id === id);
      if (index < 0) return;
      if (act === "up" && index < state.layers.length - 1) {
        [state.layers[index], state.layers[index + 1]] = [state.layers[index + 1], state.layers[index]];
      } else if (act === "down" && index > 0) {
        [state.layers[index], state.layers[index - 1]] = [state.layers[index - 1], state.layers[index]];
      } else if (act === "dup") {
        if (state.layers.length >= MAX_LAYERS) { opts.toast(i18n.t("editor.maxLayers", { n: MAX_LAYERS }), "error"); return; }
        const clone = normalizeLayer({ ...layerForSave(state.layers[index]), x: clamp(state.layers[index].x + 0.03, -0.05, 1.05), y: clamp(state.layers[index].y + 0.04, -0.05, 1.05) });
        clone.id = newId();
        state.layers.splice(index + 1, 0, clone);
        select(clone.id);
      } else if (act === "del") {
        state.layers.splice(index, 1);
        if (state.selectedId === id) select(null);
      } else return;
      pushHistory();
      scheduleRender();
    }
    function select(id) {
      state.selectedId = id;
      refreshLayersUI();
      refreshTypoUI();
      scheduleRender();
    }

    function refreshTypoUI() {
      const layer = selected();
      els.typoPanel.classList.toggle("disabled", !layer);
      for (const input of [...els.typoPanel.querySelectorAll("textarea, input, select")]) input.disabled = !layer;
      for (const btn of els.typoPanel.querySelectorAll("button")) btn.disabled = !layer;
      if (!layer) {
        els.text.value = "";
        els.layerDims.textContent = "—";
        return;
      }
      els.text.value = layer.text;
      els.font.value = [...els.font.options].some((option) => option.value === layer.font) ? layer.font : FONTS[0].v;
      els.size.value = String(layer.sizePct);
      els.sizeVal.textContent = `${layer.sizePct}%`;
      els.color.value = /^#[0-9a-f]{6}$/i.test(layer.color || "") ? layer.color : "#ffffff";
      els.bold.checked = layer.bold;
      els.opacity.value = String(Math.round(layer.opacity * 100));
      els.opacityVal.textContent = `${Math.round(layer.opacity * 100)}%`;
      els.rotation.value = String(layer.rotation);
      els.width.value = String(Math.round(layer.widthPct * 100));
      els.widthVal.textContent = `${Math.round(layer.widthPct * 100)}%`;
      for (const btn of els.typoPanel.querySelectorAll('[data-ed="align"]')) btn.classList.toggle("active", btn.dataset.value === layer.align);
      els.outlineOn.checked = layer.outline.on;
      els.outlineColor.value = /^#[0-9a-f]{6}$/i.test(layer.outline.color || "") ? layer.outline.color : "#000000";
      els.outlineWidth.value = String(Math.round(layer.outline.widthPct * 100));
      els.outlineWidthVal.textContent = `${Math.round(layer.outline.widthPct * 100)}%`;
      els.shadowOn.checked = layer.shadow.on;
      els.shadowIntensity.value = String(layer.shadow.intensity);
      els.shadowIntensityVal.textContent = `${layer.shadow.intensity}%`;
    }

    function applyToSelected(mutate, { history = false } = {}) {
      const layer = selected();
      if (!layer) return;
      mutate(layer);
      scheduleRender();
      refreshLayersUI();
      if (history) pushHistory();
    }

    /* ------------------------------------------------------------ interacao no canvas */
    function toLocal(nx, ny, b) {
      const [W, H] = dims();
      const px = nx * W - b.cx * W, py = ny * H - b.cy * H;
      const angle = (-(b.rot || 0) * Math.PI) / 180;
      return { x: px * Math.cos(angle) - py * Math.sin(angle), y: px * Math.sin(angle) + py * Math.cos(angle) };
    }
    function hitLayer(nx, ny, layer) {
      if (!layer._bounds || !(layer.text || "").trim()) return false;
      const [W, H] = dims();
      const local = toLocal(nx, ny, layer._bounds);
      return Math.abs(local.x) <= (layer._bounds.w * W) / 2 && Math.abs(local.y) <= (layer._bounds.h * H) / 2;
    }
    function hitHandle(nx, ny, corner) {
      const layer = selected();
      if (!layer || !layer._bounds) return false;
      const [W, H] = dims();
      const local = toLocal(nx, ny, layer._bounds);
      const tolerance = 14 / displayScale();
      const signX = corner === "box" || corner === "scale" ? 1 : 0;
      const signY = corner === "box" ? 1 : corner === "scale" ? -1 : 0;
      return Math.abs(local.x - (signX * layer._bounds.w * W) / 2) < tolerance
        && Math.abs(local.y - (signY * layer._bounds.h * H) / 2) < tolerance;
    }
    function applySnap(x, y, layer) {
      if (!els.snap.checked) return { x, y, guides: null };
      const [W, H] = dims();
      const b = layer._bounds || { w: 0.2, h: 0.1 };
      const bw = b.w * W, bh = b.h * H;
      const tolerance = 9 / displayScale();
      const cx = x * W, cy = y * H;
      let snappedX = null, snappedY = null;
      const safe = 0.05;
      for (const candidate of [W / 2, safe * W + bw / 2, (1 - safe) * W - bw / 2, bw / 2, W - bw / 2]) {
        if (Math.abs(cx - candidate) < tolerance) { snappedX = candidate; break; }
      }
      for (const candidate of [H / 2, safe * H + bh / 2, (1 - safe) * H - bh / 2, bh / 2, H - bh / 2]) {
        if (Math.abs(cy - candidate) < tolerance) { snappedY = candidate; break; }
      }
      return {
        x: (snappedX ?? cx) / W,
        y: (snappedY ?? cy) / H,
        guides: { v: snappedX, h: snappedY },
      };
    }

    canvas.addEventListener("pointerdown", (event) => {
      if (!state.active || !state.bg) return;
      const rect = canvas.getBoundingClientRect();
      const nx = (event.clientX - rect.left) / rect.width;
      const ny = (event.clientY - rect.top) / rect.height;
      canvas.setPointerCapture(event.pointerId);
      /* Snapshot no inicio do arraste: historico e UI so mudam se algo realmente se mover. */
      const dragSnapshot = snapshot();
      if (hitHandle(nx, ny, "box")) {
        const layer = selected();
        state.drag = { mode: "boxwidth", layerId: layer.id, snapshot: dragSnapshot };
        return;
      }
      if (hitHandle(nx, ny, "scale")) {
        const layer = selected();
        const local = toLocal(nx, ny, layer._bounds);
        state.drag = { mode: "resize", layerId: layer.id, startDist: Math.max(4, Math.hypot(local.x, local.y)), startSize: layer.sizePct, snapshot: dragSnapshot };
        return;
      }
      for (let index = state.layers.length - 1; index >= 0; index--) {
        const layer = state.layers[index];
        if (hitLayer(nx, ny, layer)) {
          select(layer.id);
          state.drag = { mode: "move", layerId: layer.id, startNx: nx, startNy: ny, startX: layer.x, startY: layer.y, guides: null, snapshot: dragSnapshot };
          return;
        }
      }
      select(null);
      const extra = bgExtra();
      if (els.bgFit.value === "cover" && (extra.x > 1 || extra.y > 1)) {
        state.drag = { mode: "pan", startNx: nx, startNy: ny, startOff: { ...state.bgOffset }, snapshot: dragSnapshot };
      }
    });
    canvas.addEventListener("pointermove", (event) => {
      const drag = state.drag;
      if (!drag || !state.active) return;
      const rect = canvas.getBoundingClientRect();
      const nx = (event.clientX - rect.left) / rect.width;
      const ny = (event.clientY - rect.top) / rect.height;
      if (drag.mode === "move") {
        const layer = state.layers.find((item) => item.id === drag.layerId);
        if (!layer) return;
        const raw = clamp(drag.startX + (nx - drag.startNx), -0.05, 1.05);
        const rawY = clamp(drag.startY + (ny - drag.startNy), -0.05, 1.05);
        const snapped = applySnap(raw, rawY, layer);
        layer.x = snapped.x;
        layer.y = snapped.y;
        drag.guides = snapped.guides;
        scheduleRender();
      } else if (drag.mode === "boxwidth") {
        const layer = state.layers.find((item) => item.id === drag.layerId);
        if (!layer || !layer._bounds) return;
        /* Largura pelo arraste do canto (caixa centrada): distancia local x2, em fração do canvas. */
        const local = toLocal(nx, ny, layer._bounds);
        layer.widthPct = clamp((Math.abs(local.x) * 2) / dims()[0], 0.1, 1);
        els.width.value = String(Math.round(layer.widthPct * 100));
        els.widthVal.textContent = `${Math.round(layer.widthPct * 100)}%`;
        scheduleRender();
      } else if (drag.mode === "resize") {
        const layer = state.layers.find((item) => item.id === drag.layerId);
        if (!layer || !layer._bounds) return;
        const local = toLocal(nx, ny, layer._bounds);
        const factor = Math.max(0.05, Math.hypot(local.x, local.y) / drag.startDist);
        layer.sizePct = clamp(drag.startSize * factor, 2, 30);
        els.size.value = String(layer.sizePct);
        els.sizeVal.textContent = `${layer.sizePct.toFixed(1)}%`;
        scheduleRender();
      } else if (drag.mode === "pan") {
        const [W, H] = dims();
        const extra = bgExtra();
        state.bgOffset = {
          x: extra.x > 1 ? clamp(drag.startOff.x + ((nx - drag.startNx) * W) / extra.x, -0.5, 0.5) : 0,
          y: extra.y > 1 ? clamp(drag.startOff.y + ((ny - drag.startNy) * H) / extra.y, -0.5, 0.5) : 0,
        };
        scheduleRender();
      }
    });
    const endDrag = () => {
      if (!state.drag) return;
      const changed = snapshot() !== state.drag.snapshot;
      state.drag = null;
      if (changed) { pushHistory(); refreshTypoUI(); }
      scheduleRender();
    };
    canvas.addEventListener("pointerup", endDrag);
    canvas.addEventListener("pointercancel", endDrag);

    /* ------------------------------------------------------------ controles do painel */
    const bindLive = (el, fn) => {
      el.addEventListener("input", () => { fn(el); scheduleRender(); });
      el.addEventListener("change", () => { fn(el); pushHistory(); });
    };
    bindLive(els.text, (el) => applyToSelected((layer) => { layer.text = el.value; }));
    bindLive(els.font, (el) => applyToSelected((layer) => { layer.font = el.value; }));
    bindLive(els.size, (el) => applyToSelected((layer) => { layer.sizePct = Number(el.value); els.sizeVal.textContent = `${Number(el.value).toFixed(1)}%`; }));
    bindLive(els.color, (el) => applyToSelected((layer) => { layer.color = el.value; }));
    bindLive(els.bold, (el) => applyToSelected((layer) => { layer.bold = el.checked; }));
    bindLive(els.opacity, (el) => applyToSelected((layer) => { layer.opacity = Number(el.value) / 100; els.opacityVal.textContent = `${el.value}%`; }));
    bindLive(els.rotation, (el) => applyToSelected((layer) => {
      const value = Number(el.value);
      if (!Number.isNaN(value)) layer.rotation = clamp(value, -180, 180);
    }));
    bindLive(els.width, (el) => applyToSelected((layer) => {
      layer.widthPct = clamp(Number(el.value) / 100, 0.1, 1);
      els.widthVal.textContent = `${el.value}%`;
    }));
    bindLive(els.outlineOn, (el) => applyToSelected((layer) => { layer.outline.on = el.checked; }));
    bindLive(els.outlineColor, (el) => applyToSelected((layer) => { layer.outline.color = el.value; }));
    bindLive(els.outlineWidth, (el) => applyToSelected((layer) => { layer.outline.widthPct = Number(el.value) / 100; els.outlineWidthVal.textContent = `${el.value}%`; }));
    bindLive(els.shadowOn, (el) => applyToSelected((layer) => { layer.shadow.on = el.checked; }));
    bindLive(els.shadowIntensity, (el) => applyToSelected((layer) => { layer.shadow.intensity = Number(el.value); els.shadowIntensityVal.textContent = `${el.value}%`; }));

    for (const btn of root.querySelectorAll('[data-ed="align"]')) {
      btn.addEventListener("click", () => applyToSelected((layer) => { layer.align = btn.dataset.value; }, { history: true }));
      btn.addEventListener("click", () => refreshTypoUI());
    }
    els.centerH.addEventListener("click", () => applyToSelected((layer) => { layer.x = 0.5; }, { history: true }));
    els.centerV.addEventListener("click", () => applyToSelected((layer) => { layer.y = 0.5; }, { history: true }));
    els.add.addEventListener("click", () => {
      if (state.layers.length >= MAX_LAYERS) { opts.toast(i18n.t("editor.maxLayers", { n: MAX_LAYERS }), "error"); return; }
      const layer = normalizeLayer({
        text: i18n.t("editor.newLayerText"), font: FONTS[0].v, sizePct: 12, color: "#ffffff", bold: true,
        align: "center", x: 0.5, y: 0.5,
        outline: { on: true, color: "#000000", widthPct: 0.09 },
        shadow: { on: true, intensity: 50 },
      });
      state.layers.push(layer);
      select(layer.id);
      pushHistory();
    });
    /* Textos-demonstracao dos presets (conteudo de EXEMPLO criado junto da camada, nao
       conteudo salvo do usuario): traduzidos no momento da aplicacao, por chave estavel. */
    const PRESET_TEXT_KEYS = {
      impacto: ["editor.presetTextImpacto"],
      tecnologia: ["editor.presetTextTecnologia"],
      minimalista: ["editor.presetTextMinimalista"],
      contraste: ["editor.presetTextContraste1", "editor.presetTextContraste2"],
    };
    for (const btn of root.querySelectorAll("[data-preset]")) {
      btn.addEventListener("click", () => {
        if (!state.bg) { opts.toast(i18n.t("editor.backgroundFirst"), "error"); return; }
        const presetLayers = STYLE_PRESETS[btn.dataset.preset] || [];
        if (state.layers.length + presetLayers.length > MAX_LAYERS) {
          opts.toast(i18n.tn("editor.freeLayers", presetLayers.length, { m: MAX_LAYERS }), "error");
          return;
        }
        const textKeys = PRESET_TEXT_KEYS[btn.dataset.preset] || [];
        const created = presetLayers.map((base, layerIndex) => {
          const layer = normalizeLayer(base);
          if (textKeys[layerIndex]) layer.text = i18n.t(textKeys[layerIndex]);
          layer.id = newId();
          state.layers.push(layer);
          return layer;
        });
        select(created[created.length - 1].id);
        pushHistory();
        opts.toast(i18n.t("editor.styleApplied", { name: btn.textContent }), "ok");
      });
    }
    els.guides.addEventListener("change", scheduleRender);
    els.snap.addEventListener("change", () => {});
    els.bgFit.addEventListener("change", () => { pushHistory(); scheduleRender(); });
    els.bgReset.addEventListener("click", () => {
      state.bgOffset = { x: 0, y: 0 };
      pushHistory();
      scheduleRender();
    });
    els.undo.addEventListener("click", undo);
    els.redo.addEventListener("click", redo);
    els.save.addEventListener("click", () => opts.onSave && opts.onSave());
    els.name.addEventListener("input", () => { state.project.name = els.name.value; notifyDirty(); });
    els.zoomFit.addEventListener("click", () => { state.zoom = null; scheduleRender(); });
    const ZOOM_STEPS = [0.25, 0.5, 0.75, 1, 1.25, 1.5, 2, 3];
    els.zoomIn.addEventListener("click", () => {
      const current = state.zoom ?? fitScale();
      state.zoom = ZOOM_STEPS.find((step) => step > current * 1.02) || ZOOM_STEPS[ZOOM_STEPS.length - 1];
      scheduleRender();
    });
    els.zoomOut.addEventListener("click", () => {
      const current = state.zoom ?? fitScale();
      const smaller = ZOOM_STEPS.filter((step) => step < current * 0.98);
      state.zoom = smaller.length ? smaller[smaller.length - 1] : ZOOM_STEPS[0];
      scheduleRender();
    });
    window.addEventListener("resize", () => { if (state.active) scheduleRender(); });

    /* ------------------------------------------------------------ atalhos de teclado */
    document.addEventListener("keydown", (event) => {
      if (!state.active) return;
      const target = event.target;
      if (target && target.closest && target.closest("input, textarea, select, [contenteditable]")) return;
      if (document.querySelector(".modal:not(.hidden)")) return;
      const mod = event.ctrlKey || event.metaKey;
      const key = event.key.toLowerCase();
      if (mod && key === "z") { event.preventDefault(); event.shiftKey ? redo() : undo(); return; }
      if (mod && key === "y") { event.preventDefault(); redo(); return; }
      if (mod && key === "d") { event.preventDefault(); layerAction(state.selectedId, "dup"); return; }
      if (mod && key === "s") { event.preventDefault(); opts.onSave && opts.onSave(); return; }
      if (mod) return;
      if (event.key === "Delete" || event.key === "Backspace") {
        if (selected()) { event.preventDefault(); layerAction(state.selectedId, "del"); }
        return;
      }
      if (event.key === "Escape") { select(null); return; }
      if (event.key.startsWith("Arrow") && selected()) {
        event.preventDefault();
        const stepPx = event.shiftKey ? 10 : 1;
        const [W, H] = dims();
        const dx = event.key === "ArrowLeft" ? -stepPx / W : event.key === "ArrowRight" ? stepPx / W : 0;
        const dy = event.key === "ArrowUp" ? -stepPx / H : event.key === "ArrowDown" ? stepPx / H : 0;
        applyToSelected((layer) => {
          layer.x = clamp(layer.x + dx, -0.05, 1.05);
          layer.y = clamp(layer.y + dy, -0.05, 1.05);
        }, { history: true });
      }
    });

    /* ------------------------------------------------------------ fundo (IA ou local) */
    function loadBackground(url, meta, label) {
      const img = new Image();
      img.onload = () => {
        const keepFit = state.bg ? els.bgFit.value : (meta.fit || "cover");
        state.bg = { img, meta };
        state.bgOffset = { x: 0, y: 0 };
        els.bgFit.value = keepFit === "contain" ? "contain" : "cover";
        if (!state.active) activate();
        resetHistory();
        scheduleRender();
        if (label) opts.toast(i18n.t("editor.backgroundSet", { label }), "ok");
      };
      img.onerror = () => {
        state.bg = null;
        opts.toast(i18n.t("editor.backgroundLoadFail"), "error");
        scheduleRender();
      };
      img.src = url;
    }
    function setBackgroundFromGeneration(gen) {
      const image = (gen.images || []).find((item) => item.kind === "generated") || (gen.images || [])[0];
      if (!image) { opts.toast(i18n.t("studio.noOutputImage"), "error"); return; }
      loadBackground(opts.urls.image(gen.id, image.id), {
        source: "generation", generation_id: gen.id, image_id: image.id,
      }, i18n.t("editor.bgFromGeneration", { seed: gen.seed }));
    }
    function setBackgroundFromReference(ref) {
      loadBackground(opts.urls.reference(ref.id), {
        source: "reference", reference_id: ref.id,
      }, i18n.t("editor.bgFromLocal", { name: ref.display_name }));
    }

    /* ------------------------------------------------------------ ciclo de vida e API */
    function activate() {
      state.active = true;
      root.hidden = false;
      toolbarHost.hidden = false;
      canvas.hidden = false;
      empty.hidden = true;
      scheduleRender();
    }
    function deactivate() {
      state.active = false;
      root.hidden = true;
      toolbarHost.hidden = true;
      canvas.hidden = true;
      empty.hidden = false;
    }
    function reset() {
      state.layers = [];
      state.selectedId = null;
      state.bg = null;
      state.bgOffset = { x: 0, y: 0 };
      state.project = { id: null, name: "" };
      els.name.value = "";
      state.zoom = null;
      state.history = [];
      state.histIndex = -1;
      clean = null;
      updateUndoButtons();
      refreshLayersUI();
      refreshTypoUI();
      deactivate();
    }
    function loadProject(project) {
      reset();
      state.project = { id: project.id, name: project.name || "" };
      els.name.value = project.name || "";
      state.layers = (project.layers || []).map((layer) => normalizeLayer(layer));
      els.guides.checked = project.canvas?.guides !== false;
      els.snap.checked = project.canvas?.snap !== false;
      els.bgFit.value = project.background?.fit === "contain" ? "contain" : "cover";
      state.bgOffset = {
        x: clamp(Number(project.background?.offset?.x || 0), -0.5, 0.5),
        y: clamp(Number(project.background?.offset?.y || 0), -0.5, 0.5),
      };
      refreshLayersUI();
      refreshTypoUI();
      activate();
      resetHistory();
      scheduleRender();
      const bg = project.background || {};
      /* Etapa 6: prefere a copia independente do fundo (sobrevive a exclusao da geracao/
         referencia original); sem copia (projetos antigos), cai na URL original. */
      if (bg.copy_image_id) {
        loadBackground(`/api/projects/${project.id}/images/${bg.copy_image_id}/content`, { ...bg }, null);
        state.bgOffset = { x: clamp(Number(bg.offset?.x || 0), -0.5, 0.5), y: clamp(Number(bg.offset?.y || 0), -0.5, 0.5) };
      } else if (bg.source === "generation" && bg.generation_id) {
        loadBackground(opts.urls.image(bg.generation_id, bg.image_id), {
          source: "generation", generation_id: bg.generation_id, image_id: bg.image_id,
        }, null);
        state.bgOffset = { x: clamp(Number(bg.offset?.x || 0), -0.5, 0.5), y: clamp(Number(bg.offset?.y || 0), -0.5, 0.5) };
      } else if (bg.source === "reference" && bg.reference_id) {
        loadBackground(opts.urls.reference(bg.reference_id), {
          source: "reference", reference_id: bg.reference_id,
        }, null);
        state.bgOffset = { x: clamp(Number(bg.offset?.x || 0), -0.5, 0.5), y: clamp(Number(bg.offset?.y || 0), -0.5, 0.5) };
      } else {
        opts.toast(i18n.t("editor.projectNoBackground"), "error");
      }
    }
    function toProject() {
      const [W, H] = dims();
      const bg = state.bg ? state.bg.meta : null;
      return {
        name: els.name.value.trim() || i18n.t("editor.untitled"),
        preset: opts.presetKey ? opts.presetKey() : null,
        width: W,
        height: H,
        generation_id: bg && bg.source === "generation" ? bg.generation_id : null,
        reference_id: bg && bg.source === "reference" ? bg.reference_id : null,
        background: {
          source: bg ? bg.source : "none",
          generation_id: bg && bg.source === "generation" ? bg.generation_id : null,
          image_id: bg && bg.source === "generation" ? bg.image_id : null,
          reference_id: bg && bg.source === "reference" ? bg.reference_id : null,
          fit: els.bgFit.value,
          offset: { ...state.bgOffset },
        },
        layers: state.layers.map(layerForSave),
        canvas: { guides: els.guides.checked, snap: els.snap.checked },
      };
    }
    async function exportBlob({ width, height }) {
      const [W, H] = dims();
      const out = document.createElement("canvas");
      out.width = width || W;
      out.height = height || H;
      renderScene(out.getContext("2d"), out.width, out.height);
      return new Promise((resolve, reject) => {
        out.toBlob((blob) => (blob ? resolve(blob) : reject(new Error(i18n.t("studio.exportRenderFail")))), "image/png");
      });
    }
    function previewDataURL() {
      const [W, H] = dims();
      const width = 360;
      const out = document.createElement("canvas");
      out.width = width;
      out.height = Math.max(40, Math.round((width * H) / W));
      renderScene(out.getContext("2d"), out.width, out.height);
      return out.toDataURL("image/png");
    }
    const setProjectMeta = ({ id, name }) => {
      state.project = { id: id || null, name: name || "" };
      if (name) els.name.value = name;
    };

    /* Etapa 10: troca de idioma — templates estaticos re-traduzidos, listas/typo re-renderizadas
       com o estado atual (o canvas nunca e recriado; projeto aberto e historico intactos). */
    function retranslate() {
      i18n.applyDOM(toolbarHost);
      i18n.applyDOM(root);
      refreshLayersUI();
      refreshTypoUI();
      notifyDirty();
    }
    i18n.onChange(retranslate);

    reset();
    return {
      activate, deactivate, reset, loadProject, toProject,
      setBackgroundFromGeneration, setBackgroundFromReference,
      exportBlob, previewDataURL, setProjectMeta, retranslate,
      hasBackground: () => Boolean(state.bg && state.bg.img.complete && state.bg.img.naturalWidth),
      setDims: () => { if (state.active) scheduleRender(); },
      isProject: () => Boolean(state.project.id),
      projectId: () => state.project.id,
      isDirty, markClean,
    };
  }

  window.createEditor = createEditor;
})();
