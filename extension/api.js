// Funções compartilhadas entre popup, opções e service worker.
const PADRAO = { server: 'http://localhost:5000', key: '' };

async function lerConfig() {
  const { config } = await chrome.storage.local.get('config');
  return { ...PADRAO, ...(config || {}) };
}

async function salvarConfig(config) {
  config.server = (config.server || PADRAO.server).trim().replace(/\/+$/, '');
  config.key = (config.key || '').trim();
  await chrome.storage.local.set({ config });
  return config;
}

// Chama a API do servidor com a chave Premium. Lança Error com a mensagem do servidor.
async function api(caminho, opcoes = {}) {
  const { server, key } = await lerConfig();
  if (!key) throw new Error('Configure sua chave de acesso nas opções da extensão (gere a chave em Minha conta, no site).');
  let resposta;
  try {
    resposta = await fetch(server + caminho, {
      ...opcoes,
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${key}`, ...(opcoes.headers || {}) },
    });
  } catch {
    throw new Error(`Não foi possível falar com o servidor (${server}). Confira sua internet ou o endereço nas opções.`);
  }
  if (resposta.status === 204) return null;
  const dados = await resposta.json().catch(() => ({}));
  if (!resposta.ok) throw new Error(dados.error || `Erro ${resposta.status}`);
  return dados;
}

const HOSTS_YT = /^(www\.|m\.|music\.)?(youtube\.com|youtu\.be|youtube-nocookie\.com)$/i;
function ehYoutube(texto) {
  try { const u = new URL(texto); return /^https?:$/.test(u.protocol) && HOSTS_YT.test(u.hostname); }
  catch { return false; }
}

// Opções de formato + qualidade (valor = tipo:formato:qualidade).
const OPCOES_FORMATO = [
  ['audio:mp3:320', 'MP3 320 kbps'], ['audio:mp3:192', 'MP3 192 kbps'], ['audio:m4a:320', 'M4A (AAC) 320 kbps'],
  ['video:mp4:1080', 'MP4 1080p'], ['video:mp4:720', 'MP4 720p'], ['video:mp4:2160', 'MP4 4K'],
  ['video:avi:720', 'AVI (DivX) 720p'],
];
