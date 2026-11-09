"""Painel administrativo: visão geral, usuários, downloads, pagamentos, suporte, logs e sistema."""
import os
import platform
import shutil
import sys
import time

from flask import Blueprint, current_app, jsonify, render_template, request

from .. import config, core, db, logs, mailer, updater
from .common import admin_required, base_url, erro, usuario_sessao

bp = Blueprint('admin', __name__)
log = logs.get('admin')
INICIO = time.time()
VIDEO_TESTE = 'https://www.youtube.com/watch?v=jNQXAC9IVRw'  # "Me at the zoo": curto e sempre disponível


def _quem():
    return usuario_sessao()['email']


@bp.get('/admin')
@admin_required
def painel():
    return render_template('admin.html')


@bp.get('/api/admin/resumo')
@admin_required
def resumo():
    dados = db.stats(days=int(request.args.get('dias', 30)))
    dados['queue'] = current_app.extensions['ytd_jobs'].counts()
    dados['ytdlp'] = updater.status()
    return jsonify(dados)


# ---------- usuários ----------

@bp.get('/api/admin/usuarios')
@admin_required
def usuarios():
    itens = db.list_users(request.args.get('q', '').strip(), limit=200)
    for u in itens:
        u['premium_active'] = db.is_premium(u)
    return jsonify({'items': itens})


@bp.post('/api/admin/usuarios')
@admin_required
def criar_usuario():
    email = ((request.get_json(silent=True) or {}).get('email') or '').strip().lower()
    if '@' not in email:
        return erro('E-mail inválido.')
    usuario = db.get_or_create_user(email)
    log.info('admin criou conta %s', logs.kv(admin=_quem(), email=email))
    return jsonify(usuario), 201


@bp.post('/api/admin/usuarios/<int:uid>/<acao>')
@admin_required
def acao_usuario(uid, acao):
    usuario = db.get_user(uid)
    if not usuario:
        return erro('Usuário não encontrado.', 404)
    dados = request.get_json(silent=True) or {}
    resposta = {'ok': True}
    if acao == 'premium':
        dias = dados.get('dias')
        dias = None if dias in (None, '', 0, '0') else int(dias)
        db.grant_premium(uid, dias)
        detalhe = f'{dias} dias' if dias else 'sem prazo'
    elif acao == 'cancelar':
        db.set_plan(usuario['email'], 'free')
        detalhe = 'voltou para Free'
    elif acao == 'admin':
        if uid == usuario_sessao()['id'] and not dados.get('on'):
            return erro('Você não pode tirar o seu próprio acesso de admin.')
        db.set_admin(uid, bool(dados.get('on')))
        detalhe = 'admin' if dados.get('on') else 'sem admin'
    elif acao == 'chave':
        resposta['key'] = db.regenerate_key(uid)
        detalhe = 'nova chave'
    else:
        return erro('Ação desconhecida.', 404)
    log.warning('admin alterou usuário %s', logs.kv(admin=_quem(), usuario=usuario['email'], acao=acao, detalhe=detalhe))
    resposta['user'] = db.get_user(uid)
    return jsonify(resposta)


# ---------- downloads e pagamentos ----------

@bp.get('/api/admin/downloads')
@admin_required
def downloads():
    a = request.args
    return jsonify({'items': db.list_downloads(a.get('status') or None, a.get('plano') or None,
                                               a.get('q', '').strip(), limit=300)})


@bp.get('/api/admin/pagamentos')
@admin_required
def pagamentos():
    return jsonify({'items': db.list_payments(limit=300), 'mode': config.PAYMENT_MODE})


# ---------- suporte ----------

@bp.get('/api/admin/suporte')
@admin_required
def tickets():
    return jsonify({'items': db.list_tickets(request.args.get('status') or None)})


@bp.get('/api/admin/suporte/<int:tid>')
@admin_required
def ticket(tid):
    t = db.get_ticket(tid)
    if not t:
        return erro('Conversa não encontrada.', 404)
    db.mark_ticket_read(tid, 'admin')
    t.pop('token_hash', None)
    usuario = db.get_user(t['user_id']) if t['user_id'] else (db.get_user_by_email(t['email']) if t['email'] else None)
    return jsonify({'ticket': t, 'messages': db.ticket_messages(tid),
                    'user': {**usuario, 'premium_active': db.is_premium(usuario)} if usuario else None})


@bp.post('/api/admin/suporte/<int:tid>/responder')
@admin_required
def responder(tid):
    t = db.get_ticket(tid)
    texto = ((request.get_json(silent=True) or {}).get('message') or '').strip()
    if not t or not texto:
        return erro('Escreva a resposta.')
    db.add_ticket_message(tid, 'admin', texto)
    log.info('suporte respondeu %s', logs.kv(admin=_quem(), ticket=tid))
    if t['email']:
        mailer.send_support_reply(t['email'], texto, base_url())
    return jsonify({'messages': db.ticket_messages(tid)})


@bp.post('/api/admin/suporte/<int:tid>/status')
@admin_required
def status_ticket(tid):
    status = (request.get_json(silent=True) or {}).get('status')
    if status not in ('open', 'closed'):
        return erro('Status inválido.')
    db.set_ticket_status(tid, status)
    log.info('suporte mudou status %s', logs.kv(admin=_quem(), ticket=tid, status=status))
    return jsonify({'ok': True})


# ---------- logs ----------

@bp.get('/api/admin/logs')
@admin_required
def ver_logs():
    a = request.args
    try:
        linhas = min(2000, max(10, int(a.get('linhas', 300))))
    except ValueError:
        linhas = 300
    arquivo = 'errors.log' if a.get('arquivo') == 'errors' else 'app.log'
    return jsonify({'items': logs.tail(linhas, a.get('nivel'), a.get('q'), arquivo)})


# ---------- sistema ----------

def _tamanho_pasta(caminho):
    total = 0
    for raiz, _, arquivos in os.walk(caminho):
        for nome in arquivos:
            try:
                total += os.path.getsize(os.path.join(raiz, nome))
            except OSError:
                pass
    return total


@bp.get('/api/admin/sistema')
@admin_required
def sistema():
    disco = shutil.disk_usage(config.DATA_DIR)
    return jsonify({
        'env': config.ENV, 'python': sys.version.split()[0], 'platform': platform.platform(),
        'uptime_s': round(time.time() - INICIO), 'dependencies': core.check_dependencies(),
        'ffmpeg': core.find_ffmpeg(), 'node': core.find_node(),
        'disk': {'total': disco.total, 'free': disco.free, 'jobs_bytes': _tamanho_pasta(config.JOBS_DIR),
                 'logs_bytes': _tamanho_pasta(config.LOG_DIR),
                 'db_bytes': os.path.getsize(config.DB_PATH) if os.path.exists(config.DB_PATH) else 0},
        'queue': current_app.extensions['ytd_jobs'].counts(),
        'ytdlp': updater.status(),
        'smtp': mailer.configured(), 'payment_mode': config.PAYMENT_MODE, 'public_url': config.PUBLIC_URL,
        'limits': {
            'free_per_hour': config.FREE_LIMIT_PER_HOUR, 'free_max_min': config.FREE_MAX_DURATION_MIN,
            'premium_daily': config.PREMIUM_DAILY_LIMIT, 'premium_max_min': config.PREMIUM_MAX_DURATION_MIN,
            'premium_price': config.PREMIUM_PRICE_BRL, 'premium_days': config.PREMIUM_DAYS,
            'workers': config.MAX_WORKERS, 'file_ttl_min': config.FILE_TTL_MIN,
        },
    })


@bp.post('/api/admin/ytdlp/atualizar')
@admin_required
def atualizar_ytdlp():
    log.warning('admin pediu atualização do yt-dlp %s', logs.kv(admin=_quem()))
    return jsonify(updater.check_and_update(motivo=f'manual ({_quem()})',
                                            force=bool((request.get_json(silent=True) or {}).get('force'))))


@bp.post('/api/admin/teste-youtube')
@admin_required
def teste_youtube():
    """Testa, a partir deste servidor, se o YouTube libera os downloads (detecta bloqueio de IP)."""
    import yt_dlp
    opts = core._base_opts(True)
    opts['skip_download'] = True
    inicio = time.time()
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(VIDEO_TESTE, download=False)
        formatos = [f for f in info.get('formats') or [] if f.get('url')]
        audio = [f for f in formatos if f.get('vcodec') == 'none']
        resultado = {'ok': bool(audio), 'seconds': round(time.time() - inicio, 1), 'formats': len(formatos),
                     'audio_formats': len(audio), 'title': info.get('title'),
                     'message': 'O YouTube está liberando os downloads para este servidor.' if audio else
                     'O vídeo abriu, mas sem formatos de áudio: o yt-dlp pode estar desatualizado.'}
    except Exception as e:
        bruto = str(e)
        bloqueio = 'Sign in to confirm' in bruto or 'not a bot' in bruto
        resultado = {'ok': False, 'seconds': round(time.time() - inicio, 1), 'blocked': bloqueio,
                     'message': ('O YouTube está bloqueando o IP deste servidor (pede login "não sou robô"). '
                                 'Veja as opções no README: cookies de uma conta ou proxy residencial.'
                                 if bloqueio else core.clean_error(bruto)),
                     'detail': bruto[-600:]}
    nivel = log.info if resultado['ok'] else log.error
    nivel('teste de acesso ao YouTube %s', logs.kv(admin=_quem(), ok=resultado['ok'], segundos=resultado['seconds'],
                                                   mensagem=resultado['message']))
    return jsonify(resultado)
