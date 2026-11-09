"""Atualização automática do yt-dlp.

- Verifica o PyPI ao iniciar (após 1 min) e depois a cada YTD_UPDATE_INTERVAL_H horas.
- Também verifica na hora quando um download falha com erro típico de "YouTube mudou".
- Atualiza com pip. Como cada download roda num processo novo (ytd/worker.py),
  a versão nova passa a valer no próximo download, sem reiniciar o servidor.
- Estado (versão, última verificação, último erro) fica no banco e aparece no painel admin.
"""
import json
import subprocess
import sys
import threading
import time
import urllib.request

from . import config, core, db, logs

log = logs.get('updater')
_lock = threading.Lock()
_ultimo_pedido = {'t': 0.0}


def latest_version():
    req = urllib.request.Request('https://pypi.org/pypi/yt-dlp/json', headers={'User-Agent': 'yt-downloader-updater'})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)['info']['version']


def _versao_tupla(v):
    try:
        return tuple(int(x) for x in str(v).split('.'))
    except ValueError:
        return (0,)


def status():
    return {
        'installed': core.ytdlp_version(),
        'latest': db.get_setting('ytdlp_latest'),
        'last_check': float(db.get_setting('ytdlp_last_check') or 0) or None,
        'last_update': float(db.get_setting('ytdlp_last_update') or 0) or None,
        'last_error': db.get_setting('ytdlp_last_error'),
        'auto': config.AUTO_UPDATE,
        'interval_h': config.UPDATE_INTERVAL_H,
    }


def check_and_update(motivo='agendado', force=False):
    """Verifica e atualiza se houver versão nova. Devolve o status."""
    if not _lock.acquire(blocking=False):
        return status()
    try:
        instalada = core.ytdlp_version()
        try:
            ultima = latest_version()
        except Exception as e:
            db.set_setting('ytdlp_last_error', f'Não foi possível consultar o PyPI: {e}')
            log.warning('falha ao consultar versão do yt-dlp %s', logs.kv(erro=e))
            return status()
        db.set_setting('ytdlp_latest', ultima)
        db.set_setting('ytdlp_last_check', time.time())
        if not force and _versao_tupla(ultima) <= _versao_tupla(instalada):
            log.info('yt-dlp em dia %s', logs.kv(versao=instalada, motivo=motivo))
            db.set_setting('ytdlp_last_error', None)
            return status()

        log.warning('atualizando yt-dlp %s', logs.kv(de=instalada, para=ultima, motivo=motivo))
        cmd = [sys.executable, '-m', 'pip', 'install', '--upgrade', '--no-input', '--disable-pip-version-check',
               'yt-dlp']
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        except Exception as e:
            db.set_setting('ytdlp_last_error', str(e))
            log.error('falha ao atualizar yt-dlp %s', logs.kv(erro=e))
            return status()
        if r.returncode != 0:
            erro = (r.stderr or r.stdout).strip()[-800:]
            db.set_setting('ytdlp_last_error', erro)
            log.error('pip falhou ao atualizar yt-dlp %s', logs.kv(saida=erro))
            return status()
        # importlib.metadata lê do disco, então já mostra a versão nova
        nova = _versao_instalada_no_disco()
        db.set_setting('ytdlp_last_update', time.time())
        db.set_setting('ytdlp_last_error', None)
        log.warning('yt-dlp atualizado %s', logs.kv(de=instalada, para=nova))
        return status()
    finally:
        _lock.release()


def _versao_instalada_no_disco():
    r = subprocess.run([sys.executable, '-c', 'from importlib.metadata import version; print(version("yt-dlp"))'],
                       capture_output=True, text=True, timeout=60)
    return r.stdout.strip() or None


def request_check(motivo):
    """Chamado quando um download falha de um jeito que sugere yt-dlp desatualizado (no máximo 1x/hora)."""
    if not config.AUTO_UPDATE or time.time() - _ultimo_pedido['t'] < 3600:
        return
    _ultimo_pedido['t'] = time.time()
    threading.Thread(target=check_and_update, args=(f'falha: {motivo[:80]}',), daemon=True).start()


def start_background():
    if not config.AUTO_UPDATE:
        log.info('atualização automática do yt-dlp desligada (YTD_AUTO_UPDATE=0)')
        return

    def loop():
        time.sleep(60)
        while True:
            try:
                check_and_update()
            except Exception:
                log.exception('erro inesperado no atualizador')
            time.sleep(max(0.5, config.UPDATE_INTERVAL_H) * 3600)

    threading.Thread(target=loop, daemon=True, name='ytd-updater').start()
    log.info('atualização automática do yt-dlp ligada %s', logs.kv(intervalo_h=config.UPDATE_INTERVAL_H))
