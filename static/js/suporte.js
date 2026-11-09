// Botão "Suporte": abre uma conversa com o suporte, que responde pelo painel admin.
// O identificador da conversa fica no navegador para o cliente continuar de onde parou.
(() => {
  'use strict';
  const CHAVE = 'ytd_suporte_token';
  const $ = (id) => document.getElementById(id);
  const botao = $('suporte-botao'), painel = $('suporte-painel'), ponto = $('suporte-ponto');
  const formNovo = $('suporte-novo'), conversa = $('suporte-conversa'), lista = $('suporte-mensagens');
  const formResp = $('suporte-responder');
  if (!botao) return;

  const ler = () => { try { return localStorage.getItem(CHAVE); } catch { return null; } };
  const gravar = (v) => { try { v ? localStorage.setItem(CHAVE, v) : localStorage.removeItem(CHAVE); } catch { /* sem storage */ } };
  let token = ler(), timer = null;

  function hora(ts) { return new Date(ts * 1000).toLocaleString('pt-BR', { dateStyle: 'short', timeStyle: 'short' }); }

  function desenhar(dados) {
    lista.replaceChildren(...dados.messages.map((m) => {
      const li = document.createElement('li');
      li.className = `sm sm-${m.sender}`;
      const p = document.createElement('p'); p.textContent = m.body;
      const t = document.createElement('time'); t.textContent = `${m.sender === 'admin' ? 'Suporte' : 'Você'} · ${hora(m.created_at)}`;
      li.append(p, t);
      return li;
    }));
    if (dados.status === 'closed') {
      const li = document.createElement('li');
      li.className = 'sm-aviso'; li.textContent = 'Conversa encerrada. Se escrever de novo, ela é reaberta.';
      lista.append(li);
    } else if (!dados.messages.some((m) => m.sender === 'admin')) {
      const li = document.createElement('li');
      li.className = 'sm-aviso'; li.textContent = 'Recebemos sua mensagem. A resposta aparece aqui e no seu e-mail.';
      lista.append(li);
    }
    lista.scrollTop = lista.scrollHeight;
  }

  async function carregar(marcarLida) {
    if (!token) return;
    try {
      const r = await fetch(`/api/suporte/${encodeURIComponent(token)}${marcarLida ? '?lido=1' : ''}`);
      if (r.status === 404) { token = null; gravar(null); mostrar(); return; }
      const dados = await r.json();
      if (marcarLida) desenhar(dados);
      ponto.hidden = marcarLida || !dados.unread;
    } catch { /* tenta de novo no próximo ciclo */ }
  }

  function mostrar() {
    formNovo.hidden = !!token;
    conversa.hidden = !token;
  }

  function abrir() {
    painel.hidden = false;
    botao.setAttribute('aria-expanded', 'true');
    mostrar();
    if (token) carregar(true);
    clearInterval(timer);
    timer = setInterval(() => carregar(true), 8000);
    (token ? formResp.message : formNovo.message).focus();
  }

  function fechar() {
    painel.hidden = true;
    botao.setAttribute('aria-expanded', 'false');
    clearInterval(timer);
    timer = setInterval(() => carregar(false), 60000);
    botao.focus();
  }

  botao.addEventListener('click', () => (painel.hidden ? abrir() : fechar()));
  $('suporte-fechar').addEventListener('click', fechar);
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && !painel.hidden) fechar(); });
  document.querySelectorAll('[data-abrir-suporte]').forEach((b) => b.addEventListener('click', abrir));
  $('suporte-nova').addEventListener('click', () => { token = null; gravar(null); mostrar(); formNovo.message.focus(); });

  async function enviar(url, corpo, form) {
    const botaoEnviar = form.querySelector('button[type="submit"]');
    const erroEl = formNovo.querySelector('.suporte-erro');
    botaoEnviar.disabled = true; erroEl.hidden = true;
    try {
      const r = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(corpo) });
      const dados = await r.json();
      if (!r.ok) throw new Error(dados.error || 'Não foi possível enviar.');
      return dados;
    } catch (e) {
      if (form === formNovo) { erroEl.textContent = e.message; erroEl.hidden = false; } else { alert(e.message); }
      return null;
    } finally {
      botaoEnviar.disabled = false;
    }
  }

  formNovo.addEventListener('submit', async (e) => {
    e.preventDefault();
    const f = formNovo;
    const dados = await enviar('/api/suporte', { name: f.name.value, email: f.email.value, message: f.message.value,
      page: location.pathname }, f);
    if (!dados) return;
    token = dados.token; gravar(token);
    f.message.value = '';
    mostrar(); desenhar(dados);
  });

  formResp.addEventListener('submit', async (e) => {
    e.preventDefault();
    const dados = await enviar(`/api/suporte/${encodeURIComponent(token)}/mensagens`, { message: formResp.message.value }, formResp);
    if (!dados) return;
    formResp.message.value = '';
    desenhar(dados);
  });
  formResp.message.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); formResp.requestSubmit(); }
  });

  if (token) { carregar(false); timer = setInterval(() => carregar(false), 60000); }
})();
