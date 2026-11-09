// Popup da extensão Premium: novo download + histórico com "baixar de novo".
(() => {
  const $ = (id) => document.getElementById(id);

  const enviar = (msg) => new Promise((ok, falha) => chrome.runtime.sendMessage(msg, (r) => {
    if (chrome.runtime.lastError) return falha(new Error(chrome.runtime.lastError.message));
    r && r.ok ? ok(r.dados) : falha(new Error(r ? r.erro : 'Sem resposta da extensão.'));
  }));

  const el = (tag, props = {}, filhos = []) => {
    const e = Object.assign(document.createElement(tag), props);
    filhos.forEach((f) => e.append(f));
    return e;
  };

  function vu(percentual) {
    const n = 24, acesos = Math.round((percentual / 100) * n);
    const barra = el('div', { className: 'vu' });
    for (let i = 0; i < n; i++) {
      const s = el('i');
      if (i < acesos) s.className = i >= 20 ? 'on pico' : 'on';
      barra.append(s);
    }
    return barra;
  }

  // ---------- abas ----------
  function abrirAba(nome) {
    for (const aba of ['baixar', 'historico']) {
      $(`aba-${aba}`).setAttribute('aria-selected', String(aba === nome));
      $(`painel-${aba}`).hidden = aba !== nome;
    }
    if (nome === 'historico') carregarHistorico();
  }
  $('aba-baixar').onclick = () => abrirAba('baixar');
  $('aba-historico').onclick = () => abrirAba('historico');
  $('abrir-opcoes').onclick = $('ir-opcoes').onclick = (e) => { e.preventDefault(); chrome.runtime.openOptionsPage(); };

  // ---------- fila de downloads ----------
  function desenharFila(jobs) {
    const lista = Object.values(jobs || {}).sort((a, b) => b.criadoEm - a.criadoEm);
    $('fila').replaceChildren(...lista.map((j) => {
      let meta;
      if (j.status === 'done') meta = el('div', { className: 'meta ok', textContent: `Salvo: ${j.file_name}` });
      else if (j.status === 'error') meta = el('div', { className: 'meta erro', textContent: j.errors?.[0]?.message || 'Erro' });
      else if (j.status === 'queued') meta = el('div', { className: 'meta', textContent: 'Na fila' });
      else meta = el('div', { className: 'meta' }, [
        el('span', { textContent: j.stage }), el('span', { textContent: `${Math.round(j.progress)}%` })]);
      const titulo = (j.titles && j.titles.length) ? j.titles.join(', ') : j.rotulo;
      const item = el('li', {}, [el('div', { className: 'titulo', textContent: titulo, title: titulo }), meta]);
      if (j.status === 'running') item.append(vu(j.progress));
      if (j.errors?.length && j.status === 'done') {
        item.append(el('div', { className: 'meta erro', textContent: `${j.errors.length} link(s) falharam.` }));
      }
      return item;
    }));
    $('limpar').hidden = !lista.some((j) => j.status === 'done' || j.status === 'error');
  }

  chrome.storage.onChanged.addListener((mudancas, area) => {
    if (area === 'session' && mudancas.jobs) desenharFila(mudancas.jobs.newValue);
  });
  $('limpar').onclick = async () => { await enviar({ tipo: 'limpar' }); desenharFila(await enviar({ tipo: 'acompanhar' })); };

  $('form').addEventListener('submit', async (ev) => {
    ev.preventDefault();
    $('erro').hidden = true;
    const urls = $('links').value.split(/[\s,]+/).map((s) => s.trim()).filter(Boolean);
    const invalido = urls.find((u) => !ehYoutube(u));
    if (!urls.length || invalido) {
      $('erro').textContent = invalido ? `Este link não é do YouTube: ${invalido}` : 'Cole pelo menos um link do YouTube.';
      $('erro').hidden = false;
      return;
    }
    const [kind, fmt, quality] = $('formato').value.split(':');
    $('baixar').disabled = true;
    try {
      await enviar({ tipo: 'iniciar', pedido: { urls, kind, fmt, quality, playlist: $('playlist').checked, rotulo: urls[0] } });
      atualizarUso();
      $('links').value = '';
    } catch (e) {
      $('erro').textContent = e.message; $('erro').hidden = false;
    } finally {
      $('baixar').disabled = false;
    }
  });

  // ---------- histórico ----------
  let atraso;
  $('busca').addEventListener('input', () => { clearTimeout(atraso); atraso = setTimeout(carregarHistorico, 250); });

  async function carregarHistorico() {
    const q = encodeURIComponent($('busca').value.trim());
    let itens = [];
    try {
      itens = (await api(`/api/history?limit=100&q=${q}`)).items;
    } catch (e) {
      $('historico').replaceChildren(el('li', { className: 'meta erro', textContent: e.message }));
      return;
    }
    $('historico-vazio').hidden = itens.length > 0;
    $('historico').replaceChildren(...itens.map((it) => {
      const data = new Date(it.created_at * 1000).toLocaleString('pt-BR', { dateStyle: 'short', timeStyle: 'short' });
      const atual = OPCOES_FORMATO.find(([v]) => v.startsWith(`${it.kind}:${it.fmt}:`));
      const seletor = el('select', { ariaLabel: 'Formato' }, OPCOES_FORMATO.map(([v, n]) =>
        el('option', { value: v, textContent: n, selected: atual && v === atual[0] })));
      const deNovo = el('button', { className: 'de-novo', textContent: 'Baixar de novo' });
      deNovo.onclick = async () => {
        const [kind, fmt, quality] = seletor.value.split(':');
        deNovo.disabled = true;
        try {
          await enviar({ tipo: 'iniciar', pedido: { historyId: it.id, kind, fmt, quality, rotulo: it.title } });
          abrirAba('baixar');
        } catch (e) { alert(e.message); } finally { deNovo.disabled = false; }
      };
      const apagar = el('button', { className: 'apagar', textContent: 'Remover', title: 'Remover do histórico' });
      apagar.onclick = async () => { await api(`/api/history/${it.id}`, { method: 'DELETE' }); carregarHistorico(); };
      return el('li', {}, [
        el('div', { className: 'titulo', textContent: it.title, title: it.title }),
        el('div', { className: 'meta' }, [el('span', { textContent: `${it.fmt.toUpperCase()} · ${data}` }),
          el('a', { href: it.url, target: '_blank', textContent: 'Abrir no YouTube' })]),
        el('div', { className: 'acoes' }, [seletor, deNovo, apagar]),
      ]);
    }));
  }

  // ---------- uso do dia ----------
  async function atualizarUso() {
    try {
      const eu = await api('/api/me');
      $('uso').textContent = `${eu.used_24h}/${eu.daily_limit} hoje`;
    } catch (e) {
      $('erro').textContent = e.message; $('erro').hidden = false;
    }
  }

  // ---------- início ----------
  $('formato').replaceChildren(...OPCOES_FORMATO.map(([v, n]) => el('option', { value: v, textContent: n })));
  (async () => {
    const { key } = await lerConfig();
    if (!key) { $('sem-chave').hidden = false; return; }
    $('app').hidden = false;
    // Preenche com a aba atual quando ela é do YouTube.
    const [aba] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (aba && aba.url && ehYoutube(aba.url)) {
      $('links').value = aba.url;
      $('playlist').checked = /[?&]list=/.test(aba.url) && !/[?&]v=/.test(aba.url);
    }
    desenharFila(await enviar({ tipo: 'acompanhar' }));
    atualizarUso();
  })();
})();
