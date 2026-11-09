// Painel administrativo: busca dados em /api/admin/* e desenha cada seção.
(() => {
  'use strict';
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => [...el.querySelectorAll(s)];
  const CSRF = document.querySelector('meta[name="csrf-token"]').content;

  // Tudo que vem de usuários (títulos, mensagens, e-mails) passa por esc() antes de virar HTML.
  const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const num = (n) => Number(n || 0).toLocaleString('pt-BR');
  const brl = (n) => Number(n || 0).toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' });
  const dataHora = (ts) => ts ? new Date(ts * 1000).toLocaleString('pt-BR', { dateStyle: 'short', timeStyle: 'short' }) : '—';
  const dataCurta = (ts) => ts ? new Date(ts * 1000).toLocaleDateString('pt-BR') : '—';
  const bytes = (b) => { b = Number(b || 0); const u = ['B', 'KB', 'MB', 'GB', 'TB']; let i = 0; while (b >= 1024 && i < u.length - 1) { b /= 1024; i++; } return `${b.toFixed(i ? 1 : 0)} ${u[i]}`; };
  const duracao = (s) => { s = Math.round(s || 0); const d = Math.floor(s / 86400), h = Math.floor(s % 86400 / 3600), m = Math.floor(s % 3600 / 60); return d ? `${d}d ${h}h` : h ? `${h}h ${m}min` : `${m} min`; };
  const tempoMidia = (s) => s ? `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, '0')}` : '—';

  async function api(caminho, opcoes = {}) {
    const r = await fetch(caminho, {
      ...opcoes,
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': CSRF, ...(opcoes.headers || {}) },
      body: opcoes.body ? JSON.stringify(opcoes.body) : undefined,
    });
    const dados = r.status === 204 ? {} : await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(dados.error || `Erro ${r.status}`);
    return dados;
  }

  function toast(texto, erro = false) {
    const t = $('#toast');
    t.textContent = texto; t.className = 'toast' + (erro ? ' toast-erro' : ''); t.hidden = false;
    clearTimeout(toast.timer); toast.timer = setTimeout(() => { t.hidden = true; }, 3500);
  }

  // ---------------------------------------------------------------- abas
  const carregadores = {};
  function abrir(aba) {
    if (!carregadores[aba]) aba = 'geral';
    $$('[data-painel]').forEach((p) => { p.hidden = p.dataset.painel !== aba; });
    $$('[data-aba]').forEach((a) => a.setAttribute('aria-current', a.dataset.aba === aba ? 'page' : 'false'));
    if (aba !== 'logs') pararLogs();
    carregadores[aba]().catch((e) => toast(e.message, true));
  }
  window.addEventListener('hashchange', () => abrir(location.hash.slice(1)));
  $$('[data-recarregar]').forEach((b) => b.addEventListener('click', () => abrir(location.hash.slice(1) || 'geral')));

  // ---------------------------------------------------------------- visão geral
  let ultimaSerie = [];
  carregadores.geral = async () => {
    const r = await api('/api/admin/resumo');
    const taxaFalha = r.downloads_24h + r.failed_24h ? Math.round(r.failed_24h / (r.downloads_24h + r.failed_24h) * 100) : 0;
    const kpis = [
      ['Downloads em 24 h', num(r.downloads_24h), `${num(r.downloads_7d)} nos últimos 7 dias`],
      ['Falhas em 24 h', num(r.failed_24h), `${taxaFalha}% dos pedidos`, taxaFalha >= 20 ? 'alerta' : ''],
      ['Premium ativos', num(r.premium_active), `${num(r.users_total)} contas, ${num(r.users_new_7d)} novas na semana`],
      ['Receita em 30 dias', brl(r.revenue_30d), `${brl(r.revenue_total)} no total`],
      ['Suporte', num(r.tickets_unread), `${num(r.tickets_open)} conversas abertas`, r.tickets_unread ? 'alerta' : ''],
      ['Fila agora', `${r.queue.running}/${r.queue.workers}`, `${r.queue.queued} esperando, ${num(r.unique_ips_24h)} IPs em 24 h`],
    ];
    $('#kpis').innerHTML = kpis.map(([rotulo, valor, sub, cls]) =>
      `<div class="kpi ${cls || ''}"><span class="kpi-rotulo">${rotulo}</span><strong>${valor}</strong><span class="kpi-sub">${sub}</span></div>`).join('');
    ultimaSerie = r.series;
    desenharGrafico(r.series);
    $('#top').innerHTML = r.top.length ? r.top.map((t) =>
      `<li><a href="${esc(t.url)}" target="_blank" rel="noopener">${esc(t.title || t.url)}</a><span>${num(t.n)}</span></li>`).join('')
      : '<li class="vazio">Nenhum download nos últimos 7 dias.</li>';
    $('#top-erros').innerHTML = r.top_errors.length ? r.top_errors.map((t) =>
      `<li><span class="erro-texto">${esc(t.error || 'sem mensagem')}</span><span>${num(t.n)}</span></li>`).join('')
      : '<li class="vazio">Nenhum erro nos últimos 7 dias.</li>';
    contadorSuporte(r.tickets_unread);
    if (r.ytdlp.last_error) toast(`yt-dlp: ${r.ytdlp.last_error}`, true);
  };

  function desenharGrafico(serie) {
    const el = $('#grafico');
    const W = 760, H = 220, ML = 36, MB = 24, MT = 8, MR = 4;
    const max = Math.max(4, ...serie.map((d) => d.free + d.premium));
    const passo = Math.pow(10, Math.floor(Math.log10(max)));
    const topo = Math.ceil(max / passo) * passo;
    const lw = (W - ML - MR) / serie.length, bw = Math.max(4, lw - 4);
    const y = (v) => MT + (H - MT - MB) * (1 - v / topo);
    let svg = `<svg viewBox="0 0 ${W} ${H}" class="svg-grafico">`;
    for (let i = 0; i <= 4; i++) {
      const v = topo * i / 4, yy = y(v);
      svg += `<line x1="${ML}" x2="${W - MR}" y1="${yy}" y2="${yy}" class="grade"/><text x="${ML - 6}" y="${yy + 4}" class="eixo" text-anchor="end">${num(Math.round(v))}</text>`;
    }
    serie.forEach((d, i) => {
      const x = ML + i * lw + (lw - bw) / 2;
      const yf = y(d.free), yp = y(d.free + d.premium);
      const hf = y(0) - yf, hp = yf - yp;
      // Premium em cima de Free, com 2px de respiro entre os segmentos; ponta arredondada só no topo.
      if (d.free) svg += `<path class="barra-free" d="${barra(x, yf, bw, hf, !d.premium)}"/>`;
      if (d.premium) svg += `<path class="barra-premium" d="${barra(x, yp, bw, Math.max(0, hp - (d.free ? 2 : 0)), true)}"/>`;
      svg += `<rect x="${ML + i * lw}" y="${MT}" width="${lw}" height="${H - MT - MB}" class="alvo" data-i="${i}"/>`;
      if (i % 5 === 0 || i === serie.length - 1) {
        svg += `<text x="${x + bw / 2}" y="${H - 6}" class="eixo" text-anchor="middle">${new Date(d.day * 1000).toLocaleDateString('pt-BR', { day: '2-digit', month: '2-digit', timeZone: 'UTC' })}</text>`;
      }
    });
    svg += '</svg><div class="dica" hidden></div>';
    el.innerHTML = svg;
    const dica = $('.dica', el);
    $$('.alvo', el).forEach((alvo) => {
      alvo.addEventListener('mouseenter', () => {
        const d = serie[alvo.dataset.i];
        alvo.classList.add('ativo');
        dica.innerHTML = `<strong>${new Date(d.day * 1000).toLocaleDateString('pt-BR', { weekday: 'short', day: '2-digit', month: '2-digit', timeZone: 'UTC' })}</strong>
          <span><i class="cor-premium"></i>Premium ${num(d.premium)}</span><span><i class="cor-free"></i>Free ${num(d.free)}</span>
          <span class="texto-fraco">Falhas ${num(d.failed)}</span>`;
        dica.hidden = false;
        const caixa = el.getBoundingClientRect(), a = alvo.getBoundingClientRect();
        const esquerda = Math.min(caixa.width - dica.offsetWidth - 4, Math.max(4, a.left - caixa.left + a.width / 2 - dica.offsetWidth / 2));
        dica.style.left = `${esquerda}px`;
      });
      alvo.addEventListener('mouseleave', () => { alvo.classList.remove('ativo'); dica.hidden = true; });
    });
    $('#grafico-tabela').innerHTML = `<table class="tabela-adm"><thead><tr><th>Dia</th><th>Free</th><th>Premium</th><th>Falhas</th></tr></thead><tbody>${
      serie.slice().reverse().map((d) => `<tr><td>${new Date(d.day * 1000).toLocaleDateString('pt-BR', { timeZone: 'UTC' })}</td><td>${d.free}</td><td>${d.premium}</td><td>${d.failed}</td></tr>`).join('')}</tbody></table>`;
  }
  function barra(x, y, w, h, arredondar) {
    if (h <= 0) return '';
    const r = arredondar ? Math.min(4, w / 2, h) : 0;
    return `M${x},${y + h} V${y + r} Q${x},${y} ${x + r},${y} H${x + w - r} Q${x + w},${y} ${x + w},${y + r} V${y + h} Z`;
  }
  $('#alternar-tabela').addEventListener('click', (e) => {
    const tabela = $('#grafico-tabela'), mostrar = tabela.hidden;
    tabela.hidden = !mostrar; $('#grafico').hidden = mostrar;
    e.target.textContent = mostrar ? 'Ver como gráfico' : 'Ver como tabela';
    e.target.setAttribute('aria-pressed', String(mostrar));
  });

  function contadorSuporte(n) {
    const c = $('#cont-suporte');
    c.hidden = !n; c.textContent = n;
  }

  // ---------------------------------------------------------------- suporte
  let ticketAberto = null;
  carregadores.suporte = async () => {
    const { items } = await api(`/api/admin/suporte?status=${$('#filtro-tickets').value}`);
    $('#tickets').innerHTML = items.length ? items.map((t) => `
      <li><button data-ticket="${t.id}" class="${t.unread_admin ? 'nao-lido' : ''} ${t.id === ticketAberto ? 'ativo' : ''}">
        <span class="t-quem">${esc(t.name || t.email || 'Visitante')}${t.unread_admin ? ' <i class="ponto"></i>' : ''}</span>
        <span class="t-ultima">${esc((t.last || '').slice(0, 90))}</span>
        <span class="t-data">${dataHora(t.updated_at)} · ${t.total} msg</span></button></li>`).join('')
      : '<li class="vazio">Nenhuma conversa aqui.</li>';
    $$('[data-ticket]').forEach((b) => b.addEventListener('click', () => abrirTicket(Number(b.dataset.ticket))));
    if (ticketAberto) abrirTicket(ticketAberto, false);
  };
  $('#filtro-tickets').addEventListener('change', () => carregadores.suporte());

  async function abrirTicket(id, recarregarLista = true) {
    ticketAberto = id;
    const { ticket, messages, user } = await api(`/api/admin/suporte/${id}`);
    const fechado = ticket.status === 'closed';
    $('#ticket-detalhe').innerHTML = `
      <div class="ticket-topo">
        <div><strong>${esc(ticket.name || 'Visitante')}</strong>
          <span class="texto-fraco">${esc(ticket.email || 'sem e-mail')}${user ? ` · ${user.premium_active ? 'Premium' : 'Free'}` : ''}</span>
          ${ticket.page ? `<span class="texto-fraco">Página: ${esc(ticket.page)}</span>` : ''}</div>
        <button class="btn-sec" id="status-ticket">${fechado ? 'Reabrir' : 'Encerrar conversa'}</button>
      </div>
      <ol class="msgs">${messages.map((m) => `<li class="msg msg-${m.sender}"><p>${esc(m.body)}</p><time>${m.sender === 'admin' ? 'Suporte' : 'Cliente'} · ${dataHora(m.created_at)}</time></li>`).join('')}</ol>
      <form id="responder" class="responder"><textarea required rows="3" placeholder="Escreva a resposta${ticket.email ? ' (o cliente também recebe por e-mail)' : ''}" aria-label="Resposta"></textarea><button class="btn">Responder</button></form>`;
    const lista = $('.msgs'); lista.scrollTop = lista.scrollHeight;
    $('#responder').addEventListener('submit', async (e) => {
      e.preventDefault();
      const campo = $('textarea', e.target);
      try { await api(`/api/admin/suporte/${id}/responder`, { method: 'POST', body: { message: campo.value } }); toast('Resposta enviada.'); abrirTicket(id); }
      catch (err) { toast(err.message, true); }
    });
    $('#status-ticket').addEventListener('click', async () => {
      await api(`/api/admin/suporte/${id}/status`, { method: 'POST', body: { status: fechado ? 'open' : 'closed' } });
      toast(fechado ? 'Conversa reaberta.' : 'Conversa encerrada.');
      carregadores.suporte();
    });
    if (recarregarLista) carregadores.suporte();
  }

  // ---------------------------------------------------------------- usuários
  let atrasoUsuarios;
  carregadores.usuarios = async () => {
    const { items } = await api(`/api/admin/usuarios?q=${encodeURIComponent($('#busca-usuarios').value)}`);
    $('#tabela-usuarios').innerHTML = `<thead><tr><th>E-mail</th><th>Plano</th><th>Válido até</th><th>Downloads</th><th>Pago</th><th>Último login</th><th>Ações</th></tr></thead><tbody>${
      items.map((u) => `<tr>
        <td>${esc(u.email)}${u.is_admin ? ' <span class="etiqueta">admin</span>' : ''}</td>
        <td>${u.premium_active ? '<span class="etiqueta etiqueta-premium">Premium</span>' : 'Free'}</td>
        <td>${u.premium_active ? (u.premium_until ? dataCurta(u.premium_until) : 'sem prazo') : '—'}</td>
        <td>${num(u.downloads)}</td><td>${brl(u.paid)}</td><td>${dataHora(u.last_login)}</td>
        <td class="acoes">
          <button class="link-botao" data-u="${u.id}" data-acao="premium">+30 dias</button>
          ${u.premium_active ? `<button class="link-botao" data-u="${u.id}" data-acao="cancelar">Cancelar Premium</button>` : ''}
          <button class="link-botao" data-u="${u.id}" data-acao="chave">Nova chave</button>
          <button class="link-botao" data-u="${u.id}" data-acao="admin" data-on="${u.is_admin ? 0 : 1}">${u.is_admin ? 'Tirar admin' : 'Tornar admin'}</button>
        </td></tr>`).join('')}</tbody>`;
    $$('#tabela-usuarios [data-acao]').forEach((b) => b.addEventListener('click', () => acaoUsuario(b)));
  };
  async function acaoUsuario(b) {
    const { u, acao, on } = b.dataset;
    const confirmar = { cancelar: 'Cancelar o Premium deste usuário agora?', chave: 'Gerar nova chave? A atual para de funcionar.' }[acao];
    if (confirmar && !confirm(confirmar)) return;
    try {
      const r = await api(`/api/admin/usuarios/${u}/${acao}`, { method: 'POST', body: { dias: 30, on: on === '1' } });
      if (r.key) prompt('Nova chave (copie e envie ao cliente):', r.key);
      toast('Usuário atualizado.');
      carregadores.usuarios();
    } catch (e) { toast(e.message, true); }
  }
  $('#busca-usuarios').addEventListener('input', () => { clearTimeout(atrasoUsuarios); atrasoUsuarios = setTimeout(carregadores.usuarios, 300); });
  $('#novo-usuario').addEventListener('submit', async (e) => {
    e.preventDefault();
    const campo = $('input', e.target);
    try { await api('/api/admin/usuarios', { method: 'POST', body: { email: campo.value } }); campo.value = ''; toast('Conta criada.'); carregadores.usuarios(); }
    catch (err) { toast(err.message, true); }
  });

  // ---------------------------------------------------------------- downloads
  let atrasoDownloads;
  carregadores.downloads = async () => {
    const q = new URLSearchParams({ q: $('#busca-downloads').value, status: $('#filtro-status').value, plano: $('#filtro-plano').value });
    const { items } = await api(`/api/admin/downloads?${q}`);
    const nomes = { done: 'Concluído', error: 'Erro', rejected: 'Recusado' };
    $('#tabela-downloads').innerHTML = `<thead><tr><th>Quando</th><th>Título</th><th>Formato</th><th>Duração</th><th>Tamanho</th><th>Plano</th><th>Quem</th><th>Situação</th></tr></thead><tbody>${
      items.map((d) => `<tr>
        <td>${dataHora(d.created_at)}</td>
        <td class="col-titulo"><a href="${esc(d.url)}" target="_blank" rel="noopener">${esc(d.title || d.url)}</a>${d.error ? `<span class="erro-texto">${esc(d.error)}</span>` : ''}</td>
        <td>${esc((d.fmt || '').toUpperCase())} ${esc(d.quality || '')}</td><td>${tempoMidia(d.media_duration)}</td>
        <td>${d.size_bytes ? bytes(d.size_bytes) : '—'}</td><td>${d.plan === 'premium' ? 'Premium' : 'Free'}${d.channel === 'extensao' ? ' (extensão)' : ''}</td>
        <td>${esc(d.email || d.ip || '')}</td><td><span class="situacao situacao-${esc(d.status)}">${nomes[d.status] || esc(d.status)}</span></td></tr>`).join('')
      || '<tr><td colspan="8" class="vazio">Nenhum download encontrado.</td></tr>'}</tbody>`;
  };
  $('#busca-downloads').addEventListener('input', () => { clearTimeout(atrasoDownloads); atrasoDownloads = setTimeout(carregadores.downloads, 300); });
  ['#filtro-status', '#filtro-plano'].forEach((s) => $(s).addEventListener('change', () => carregadores.downloads()));

  // ---------------------------------------------------------------- pagamentos
  carregadores.pagamentos = async () => {
    const { items, mode } = await api('/api/admin/pagamentos');
    $('#modo-pagamento').textContent = mode === 'mercadopago' ? 'Mercado Pago (real)' : 'Modo simulado (sem cobrança)';
    const nomes = { approved: 'Aprovado', pending: 'Pendente', rejected: 'Recusado', cancelled: 'Cancelado', refunded: 'Estornado', review: 'Revisar valor' };
    $('#tabela-pagamentos').innerHTML = `<thead><tr><th>Data</th><th>Cliente</th><th>Valor</th><th>Dias</th><th>Situação</th><th>Meio</th><th>ID externo</th></tr></thead><tbody>${
      items.map((p) => `<tr><td>${dataHora(p.created_at)}</td><td>${esc(p.email)}</td><td>${brl(p.amount)}</td><td>${p.days}</td>
        <td><span class="situacao situacao-${p.status === 'approved' ? 'done' : p.status === 'pending' ? 'pending' : 'error'}">${nomes[p.status] || esc(p.status)}</span></td>
        <td>${esc(p.detail || p.provider)}</td><td>${esc(p.external_id || '—')}</td></tr>`).join('')
      || '<tr><td colspan="7" class="vazio">Nenhum pagamento ainda.</td></tr>'}</tbody>`;
  };

  // ---------------------------------------------------------------- logs
  let timerLogs, atrasoLogs;
  carregadores.logs = async () => {
    const q = new URLSearchParams({ arquivo: $('#log-arquivo').value, nivel: $('#log-nivel').value, q: $('#log-busca').value, linhas: 400 });
    const { items } = await api(`/api/admin/logs?${q}`);
    $('#logs').innerHTML = items.length ? items.map((l) =>
      `<div class="log log-${esc(l.level.toLowerCase())}"><time>${esc(l.time)}</time><span class="log-nivel">${esc(l.level)}</span><span class="log-origem">${esc(l.source.replace('ytd.', ''))}</span><span class="log-msg">${esc(l.message)}</span></div>`).join('')
      : '<p class="vazio">Nenhuma linha com esse filtro.</p>';
    if ($('#log-auto').checked && !timerLogs) timerLogs = setInterval(() => carregadores.logs().catch(() => {}), 4000);
  };
  function pararLogs() { clearInterval(timerLogs); timerLogs = null; }
  ['#log-arquivo', '#log-nivel'].forEach((s) => $(s).addEventListener('change', () => carregadores.logs()));
  $('#log-busca').addEventListener('input', () => { clearTimeout(atrasoLogs); atrasoLogs = setTimeout(carregadores.logs, 300); });
  $('#log-auto').addEventListener('change', (e) => { pararLogs(); if (e.target.checked) carregadores.logs(); });

  // ---------------------------------------------------------------- sistema
  const linhas = (pares) => pares.map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join('');
  function mostrarYtdlp(s) {
    $('#ytdlp').innerHTML = linhas([
      ['Instalada', esc(s.installed || '?')], ['Mais nova', esc(s.latest || 'ainda não verificado')],
      ['Última verificação', dataHora(s.last_check)], ['Última atualização', dataHora(s.last_update)],
      ['Automática', s.auto ? `ligada, a cada ${s.interval_h} h e quando downloads falham` : 'desligada (YTD_AUTO_UPDATE=0)'],
      ...(s.last_error ? [['Último erro', `<span class="erro-texto">${esc(s.last_error)}</span>`]] : []),
    ]);
  }
  carregadores.sistema = async () => {
    const s = await api('/api/admin/sistema');
    mostrarYtdlp(s.ytdlp);
    $('#servidor').innerHTML = linhas([
      ['Ambiente', esc(s.env)], ['Ligado há', duracao(s.uptime_s)], ['Python', esc(s.python)], ['Sistema', esc(s.platform)],
      ['FFmpeg', s.ffmpeg ? 'encontrado' : '<span class="erro-texto">não encontrado</span>'],
      ['Node.js', s.node ? 'encontrado' : '<span class="erro-texto">não encontrado</span>'],
      ['Disco livre', `${bytes(s.disk.free)} de ${bytes(s.disk.total)}`],
      ['Arquivos temporários', bytes(s.disk.jobs_bytes)], ['Logs', bytes(s.disk.logs_bytes)], ['Banco de dados', bytes(s.disk.db_bytes)],
      ['Fila', `${s.queue.running} baixando, ${s.queue.queued} esperando (${s.queue.workers} simultâneos)`],
    ]);
    const l = s.limits;
    $('#limites').innerHTML = linhas([
      ['Endereço público', esc(s.public_url || 'não definido (YTD_PUBLIC_URL)')],
      ['E-mail (SMTP)', s.smtp ? 'configurado' : '<span class="erro-texto">não configurado: links de login só no log</span>'],
      ['Pagamento', s.payment_mode === 'mercadopago' ? 'Mercado Pago' : 'simulado (sem cobrança)'],
      ['Free', `${l.free_per_hour}/hora, até ${l.free_max_min} min por vídeo`],
      ['Premium', `${brl(l.premium_price)} por ${l.premium_days} dias, ${l.premium_daily}/dia, até ${l.premium_max_min} min`],
      ['Arquivos prontos', `apagados após ${l.file_ttl_min} min`],
    ]);
  };
  $('#testar-youtube').addEventListener('click', async (e) => {
    const alvo = $('#resultado-youtube');
    e.target.disabled = true; alvo.className = 'resultado-teste'; alvo.textContent = 'Testando, leva até 30 segundos…';
    try {
      const r = await api('/api/admin/teste-youtube', { method: 'POST' });
      alvo.className = 'resultado-teste ' + (r.ok ? 'ok' : 'falha');
      alvo.innerHTML = `<strong>${r.ok ? 'Funcionando' : r.blocked ? 'Bloqueado' : 'Falhou'}</strong> ${esc(r.message)}
        <span class="texto-fraco">${r.seconds}s${r.formats ? `, ${r.formats} formatos` : ''}</span>`;
    } catch (err) { alvo.className = 'resultado-teste falha'; alvo.textContent = err.message; }
    e.target.disabled = false;
  });
  $('#atualizar-ytdlp').addEventListener('click', async (e) => {
    const alvo = $('#resultado-ytdlp');
    e.target.disabled = true; alvo.className = 'resultado-teste'; alvo.textContent = 'Verificando…';
    try {
      const s = await api('/api/admin/ytdlp/atualizar', { method: 'POST', body: {} });
      mostrarYtdlp(s);
      alvo.className = 'resultado-teste ' + (s.last_error ? 'falha' : 'ok');
      alvo.textContent = s.last_error ? s.last_error : `Pronto. Versão em uso: ${s.installed}.`;
    } catch (err) { alvo.className = 'resultado-teste falha'; alvo.textContent = err.message; }
    e.target.disabled = false;
  });

  abrir(location.hash.slice(1) || 'geral');
  // Atualiza o contador de mensagens novas do suporte a cada 30 s.
  setInterval(() => api('/api/admin/resumo').then((r) => contadorSuporte(r.tickets_unread)).catch(() => {}), 30000);
})();
