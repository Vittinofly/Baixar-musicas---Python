// Página inicial: prévia do vídeo, escolha de formato/qualidade, download com progresso e entrega do arquivo.
(() => {
  'use strict';

  const FORMATOS = {
    audio: [['mp3', 'MP3'], ['m4a', 'M4A']],
    video: [['mp4', 'MP4'], ['avi', 'AVI (DivX)']],
  };
  const AVI_MAX = 720;
  const HOSTS_YT = /^(www\.|m\.|music\.)?(youtube\.com|youtu\.be|youtube-nocookie\.com)$/i;
  const CSRF = document.querySelector('meta[name="csrf-token"]').content;

  const $ = (id) => document.getElementById(id);
  const form = $('form'), url = $('url'), botao = $('baixar'), formatos = $('formatos'), qualidade = $('qualidade');
  const medidor = $('medidor'), segmentos = $('segmentos'), etapa = $('etapa'), pct = $('pct');
  const resultado = $('resultado'), arquivoNome = $('arquivo-nome'), salvar = $('salvar');
  const aviso = $('aviso'), uso = $('uso'), colar = $('colar');
  const previa = $('previa'), capa = $('previa-capa'), previaTitulo = $('previa-titulo');
  const previaMeta = $('previa-meta'), previaAlerta = $('previa-alerta');

  const plano = form.dataset.plano;
  const opcoesAudio = form.dataset.audio.split(',').map(Number);
  const opcoesVideo = form.dataset.video.split(',').map(Number);
  const maxMin = Number(form.dataset.maxMin);

  // ---------- medidor ----------
  let totalSeg = 0;
  function montarMedidor() {
    const n = window.matchMedia('(max-width: 640px)').matches ? 24 : 40;
    if (n === totalSeg) return;
    totalSeg = n;
    segmentos.style.setProperty('--n', n);
    segmentos.innerHTML = '';
    for (let i = 0; i < n; i++) {
      const s = document.createElement('i');
      s.style.setProperty('--i', i);
      if (i >= Math.floor(n * 0.85)) s.classList.add('zona-pico');
      segmentos.appendChild(s);
    }
  }
  let ultimoPct = 0;
  function medir(percentual, estado) {
    ultimoPct = percentual;
    medidor.dataset.estado = estado;
    const acesos = Math.round((percentual / 100) * totalSeg);
    [...segmentos.children].forEach((s, i) => {
      s.classList.toggle('on', i < acesos);
      s.classList.toggle('pico', i < acesos && s.classList.contains('zona-pico'));
    });
    pct.textContent = estado === 'parado' || estado === 'esperando' ? '' : `${Math.round(percentual)}%`;
  }
  montarMedidor();
  window.addEventListener('resize', () => { montarMedidor(); medir(ultimoPct, medidor.dataset.estado); });

  // ---------- formato e qualidade ----------
  function desenharFormatos() {
    const kind = form.kind.value;
    const legenda = formatos.querySelector('legend').outerHTML;
    formatos.innerHTML = legenda + FORMATOS[kind].map(([valor, nome], i) =>
      `<label><input type="radio" name="fmt" value="${valor}"${i === 0 ? ' checked' : ''}><span>${nome}</span></label>`
    ).join('');
    formatos.querySelectorAll('input').forEach((r) => r.addEventListener('change', desenharQualidade));
    desenharQualidade();
  }
  function desenharQualidade() {
    const kind = form.kind.value, fmt = form.fmt.value;
    let opcoes, padrao, rotulo;
    if (kind === 'audio') {
      opcoes = opcoesAudio; padrao = Number(form.dataset.padraoAudio); rotulo = (q) => `${q} kbps`;
    } else {
      opcoes = opcoesVideo.filter((h) => fmt !== 'avi' || h <= AVI_MAX);
      padrao = Math.min(Number(form.dataset.padraoVideo), Math.max(...opcoes));
      rotulo = (q) => (q === 2160 ? '4K (2160p)' : `${q}p`);
    }
    qualidade.innerHTML = opcoes.slice().reverse().map((q) =>
      `<option value="${q}"${q === padrao ? ' selected' : ''}>${rotulo(q)}</option>`).join('');
    if (plano === 'free') {
      const extra = kind === 'audio' ? '320 kbps' : 'até 4K';
      qualidade.insertAdjacentHTML('beforeend', `<option disabled>${extra} no Premium</option>`);
    }
  }
  form.querySelectorAll('input[name="kind"]').forEach((r) => r.addEventListener('change', desenharFormatos));
  desenharFormatos();

  // ---------- utilidades ----------
  function ehYoutube(texto) {
    try { const u = new URL(texto); return /^https?:$/.test(u.protocol) && HOSTS_YT.test(u.hostname); }
    catch { return false; }
  }
  function mostrarAviso(msg, upgrade) {
    aviso.textContent = msg + ' ';
    if (upgrade) {
      const a = document.createElement('a');
      a.href = '#planos'; a.textContent = 'Ver o Premium';
      aviso.appendChild(a);
    }
    aviso.hidden = false;
  }
  function ocupado(sim) {
    botao.disabled = sim;
    botao.textContent = sim ? 'Baixando…' : 'Baixar';
  }
  async function atualizarUso() {
    try {
      const p = await (await fetch('/api/plans')).json();
      if (p.current === 'premium') {
        const restam = Math.max(0, p.premium.daily_limit - (p.premium.used_24h || 0));
        uso.textContent = `Premium ativo: ${restam} de ${p.premium.daily_limit} downloads disponíveis nas próximas 24 horas, vídeos de até ${p.premium.max_duration_min} min.`;
      } else {
        const restam = Math.max(0, p.free.limit_per_hour - p.free.used_this_hour);
        uso.textContent = `Plano Free: ${restam} de ${p.free.limit_per_hour} downloads disponíveis nesta hora, vídeos de até ${p.free.max_duration_min} min.`;
      }
    } catch { /* mantém o texto padrão */ }
  }
  function entregar(job) {
    const a = document.createElement('a');
    a.href = job.download_url; a.download = job.file_name || '';
    document.body.appendChild(a); a.click(); a.remove();
    salvar.href = job.download_url;
    arquivoNome.textContent = job.file_name;
    resultado.hidden = false;
  }

  // ---------- prévia ----------
  let previaAtual = '';
  let atrasoPrevia;
  async function carregarPrevia() {
    const link = url.value.trim();
    if (!ehYoutube(link)) { previa.hidden = true; previaAtual = ''; return; }
    if (link === previaAtual) return;
    previaAtual = link;
    previaAlerta.hidden = true;
    try {
      // 1) rápido: título, canal e capa
      const r = await fetch(`/api/preview?url=${encodeURIComponent(link)}`);
      const d = await r.json();
      if (link !== previaAtual) return;
      if (r.ok && d.title) {
        capa.src = d.thumbnail || ''; capa.hidden = !d.thumbnail;
        previaTitulo.textContent = d.title;
        previaMeta.textContent = d.channel || '';
        previa.hidden = false;
      }
      // 2) completo: duração (leva alguns segundos)
      previaMeta.textContent = [d.channel, 'calculando duração…'].filter(Boolean).join(' · ');
      const r2 = await fetch(`/api/preview?full=1&url=${encodeURIComponent(link)}`);
      const f = await r2.json();
      if (link !== previaAtual) return;
      if (!r2.ok) {
        previaMeta.textContent = d.channel || '';
        if (r2.status === 422) { previaAlerta.textContent = f.error; previaAlerta.hidden = false; }
        return;
      }
      if (!d.title) {
        capa.src = f.thumbnail || ''; capa.hidden = !f.thumbnail;
        previaTitulo.textContent = f.title || 'Vídeo do YouTube';
        previa.hidden = false;
      }
      const partes = [f.channel || d.channel];
      if (f.playlist) partes.push(`playlist com ${f.count} vídeos`);
      else if (f.duration_text) partes.push(f.duration_text);
      previaMeta.textContent = partes.filter(Boolean).join(' · ');
      if (f.is_live) {
        previaAlerta.textContent = 'Transmissão ao vivo não pode ser baixada.';
        previaAlerta.hidden = false;
      } else if (f.playlist && plano === 'free') {
        previaAlerta.textContent = 'Playlists inteiras são do Premium. Abra um vídeo da playlist e copie o link dele.';
        previaAlerta.hidden = false;
      } else if (f.too_long) {
        previaAlerta.textContent = plano === 'free'
          ? `Passa do limite de ${maxMin} min do plano Free. No Premium o limite é maior.`
          : `Passa do limite de ${maxMin} min por vídeo.`;
        previaAlerta.hidden = false;
      }
    } catch { /* prévia é opcional */ }
  }
  url.addEventListener('input', () => {
    url.removeAttribute('aria-invalid');
    clearTimeout(atrasoPrevia);
    atrasoPrevia = setTimeout(carregarPrevia, 400);
  });

  // ---------- fluxo de download ----------
  async function acompanhar(id) {
    while (true) {
      await new Promise((ok) => setTimeout(ok, 800));
      let job;
      try {
        const r = await fetch(`/api/jobs/${id}`);
        job = await r.json();
        if (!r.ok) throw new Error(job.error || 'Falha ao consultar o download.');
      } catch {
        medir(0, 'erro'); etapa.textContent = 'Conexão com o servidor perdida.';
        mostrarAviso('Recarregue a página e tente de novo.');
        return;
      }
      if (job.status === 'queued') {
        medir(0, 'esperando');
        etapa.textContent = job.queue_position > 1 ? `Na fila, posição ${job.queue_position}` : 'Na fila, começa em instantes';
      } else if (job.status === 'running') {
        const prep = job.stage === 'Preparando';
        medir(job.progress, prep ? 'esperando' : 'ativo');
        etapa.textContent = prep ? 'Buscando informações do vídeo…' : job.stage;
      } else if (job.status === 'done') {
        medir(100, 'pronto');
        etapa.textContent = 'Pronto. O arquivo foi enviado para seus downloads.';
        entregar(job);
        return;
      } else {
        medir(100, 'erro');
        etapa.textContent = 'Não foi possível baixar.';
        const msg = (job.errors[0] && job.errors[0].message) || 'Erro desconhecido.';
        mostrarAviso(msg, /limite/.test(msg) && plano === 'free');
        return;
      }
    }
  }

  form.addEventListener('submit', async (ev) => {
    ev.preventDefault();
    aviso.hidden = true; resultado.hidden = true;
    const link = url.value.trim();
    if (!ehYoutube(link)) {
      url.setAttribute('aria-invalid', 'true');
      mostrarAviso(link ? 'Esse link não é do YouTube. Copie o endereço da página do vídeo.' : 'Cole o link de um vídeo do YouTube.');
      url.focus();
      return;
    }
    ocupado(true);
    medir(0, 'esperando');
    etapa.textContent = 'Enviando pedido…';
    try {
      const r = await fetch('/api/jobs', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': CSRF },
        body: JSON.stringify({ url: link, kind: form.kind.value, fmt: form.fmt.value, quality: qualidade.value }),
      });
      const dados = await r.json();
      if (!r.ok) {
        medir(0, 'parado'); etapa.textContent = 'Pronto para baixar.';
        mostrarAviso(dados.error || 'Não foi possível iniciar o download.', dados.upgrade);
        return;
      }
      await acompanhar(dados.id);
    } catch {
      medir(0, 'erro'); etapa.textContent = 'Servidor fora do ar.';
      mostrarAviso('Não foi possível falar com o servidor. Tente de novo em instantes.');
    } finally {
      ocupado(false);
      atualizarUso();
    }
  });

  if (navigator.clipboard && navigator.clipboard.readText) {
    colar.hidden = false;
    colar.addEventListener('click', async () => {
      try { url.value = (await navigator.clipboard.readText()).trim(); url.dispatchEvent(new Event('input')); url.focus(); }
      catch { url.focus(); }
    });
  }

  atualizarUso();
})();
