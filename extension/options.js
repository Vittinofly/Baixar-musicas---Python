(async () => {
  const $ = (id) => document.getElementById(id);
  const config = await lerConfig();
  $('server').value = config.server;
  $('key').value = config.key;

  $('form').addEventListener('submit', (ev) => {
    ev.preventDefault();
    const res = $('resultado');
    let origem;
    try {
      origem = new URL($('server').value.trim()).origin + '/*';
    } catch {
      res.className = 'resultado erro'; res.textContent = 'Endereço do servidor inválido.';
      return;
    }
    // O pedido de permissão precisa acontecer direto no clique (exigência do Firefox).
    chrome.permissions.request({ origins: [origem] }).then(async (ok) => {
      if (!ok) { res.className = 'resultado erro'; res.textContent = 'Permissão para acessar o servidor negada.'; return; }
      res.className = 'resultado'; res.textContent = 'Testando…';
      await salvarConfig({ server: $('server').value, key: $('key').value });
      try {
        const eu = await api('/api/me');
        res.className = 'resultado ok';
        const ate = eu.premium_until ? ` até ${new Date(eu.premium_until * 1000).toLocaleDateString('pt-BR')}` : '';
        res.textContent = `Conectado: ${eu.email}, plano ${eu.plan}${ate}.`;
      } catch (e) {
        res.className = 'resultado erro';
        res.textContent = e.message;
      }
    });
  });
})();
