// Service worker: cria downloads na API, acompanha o progresso e salva o arquivo com chrome.downloads.
// Assim o download continua mesmo depois que o popup fecha.
// Chrome/Edge (service worker) carregam api.js aqui; no Firefox ele já vem em background.scripts.
if (typeof importScripts === 'function') importScripts('api.js');

const INTERVALO_MS = 1000;
let acompanhando = false;

async function lerJobs() {
  const { jobs } = await chrome.storage.session.get('jobs');
  return jobs || {};
}
async function gravarJobs(jobs) {
  await chrome.storage.session.set({ jobs });
}

async function iniciar(pedido) {
  const job = pedido.historyId
    ? await api(`/api/history/${pedido.historyId}/redownload`, {
        method: 'POST', body: JSON.stringify({ kind: pedido.kind, fmt: pedido.fmt, quality: pedido.quality }),
      })
    : await api('/api/jobs', {
        method: 'POST',
        body: JSON.stringify({ urls: pedido.urls, kind: pedido.kind, fmt: pedido.fmt, quality: pedido.quality, playlist: !!pedido.playlist }),
      });
  const jobs = await lerJobs();
  jobs[job.id] = { ...job, rotulo: pedido.rotulo || pedido.urls?.[0] || 'Download', criadoEm: Date.now() };
  await gravarJobs(jobs);
  acompanhar();
  return job;
}

async function acompanhar() {
  if (acompanhando) return;
  acompanhando = true;
  try {
    while (true) {
      const jobs = await lerJobs();
      const pendentes = Object.values(jobs).filter((j) => j.status === 'queued' || j.status === 'running');
      if (!pendentes.length) break;
      const { server } = await lerConfig();
      for (const j of pendentes) {
        try {
          const atual = await api(`/api/jobs/${j.id}`);
          jobs[j.id] = { ...j, ...atual };
          if (atual.status === 'done' && !j.entregue) {
            await chrome.downloads.download({ url: server + atual.download_url, saveAs: false });
            jobs[j.id].entregue = true;
          }
        } catch (e) {
          jobs[j.id] = { ...j, status: 'error', errors: [{ message: e.message }] };
        }
      }
      // Gravar no storage também mantém o service worker acordado durante o download.
      await gravarJobs(jobs);
      await new Promise((ok) => setTimeout(ok, INTERVALO_MS));
    }
  } finally {
    acompanhando = false;
  }
}

async function limparConcluidos() {
  const jobs = await lerJobs();
  for (const [id, j] of Object.entries(jobs)) {
    if (j.status === 'done' || j.status === 'error') delete jobs[id];
  }
  await gravarJobs(jobs);
}

chrome.runtime.onMessage.addListener((msg, _sender, responder) => {
  const acoes = {
    iniciar: () => iniciar(msg.pedido),
    acompanhar: async () => { acompanhar(); return lerJobs(); },
    limpar: limparConcluidos,
  };
  const acao = acoes[msg.tipo];
  if (!acao) return false;
  acao().then((dados) => responder({ ok: true, dados }), (e) => responder({ ok: false, erro: e.message }));
  return true; // resposta assíncrona
});

// Menu do botão direito em páginas e links do YouTube.
const PADROES = ['*://*.youtube.com/*', '*://youtu.be/*'];
chrome.runtime.onInstalled.addListener((detalhes) => {
  chrome.contextMenus.removeAll(() => {
    chrome.contextMenus.create({ id: 'mp3', title: 'Baixar MP3 com YT Downloader', contexts: ['page', 'link'],
      documentUrlPatterns: PADROES, targetUrlPatterns: PADROES });
    chrome.contextMenus.create({ id: 'mp4', title: 'Baixar vídeo MP4 com YT Downloader', contexts: ['page', 'link'],
      documentUrlPatterns: PADROES, targetUrlPatterns: PADROES });
  });
  if (detalhes.reason === 'install') chrome.runtime.openOptionsPage();
});

chrome.contextMenus.onClicked.addListener(async (info) => {
  const url = info.linkUrl || info.pageUrl;
  if (!ehYoutube(url)) return;
  try {
    await iniciar({ urls: [url], kind: info.menuItemId === 'mp4' ? 'video' : 'audio',
      fmt: info.menuItemId === 'mp4' ? 'mp4' : 'mp3', rotulo: url });
  } catch (e) {
    console.warn('YT Downloader:', e.message);
  }
});
