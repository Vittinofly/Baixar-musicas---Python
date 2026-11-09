"""API JSON: downloads (site e extensão), prévia, histórico Premium, suporte e webhook de pagamento."""
import os
import threading
import time
from collections import defaultdict, deque

from flask import Blueprint, current_app, jsonify, request, send_file

from .. import config, core, db, logs, payments
from ..jobs import Job
from .common import erro, ip_cliente, usuario_api, usuario_sessao

bp = Blueprint('api', __name__, url_prefix='/api')
log = logs.get('api')
log_sup = logs.get('suporte')
log_pay = logs.get('payments')


class Limitador:
    """Limite simples por chave (IP) numa janela de tempo, em memória."""

    def __init__(self, maximo, janela_s):
        self.maximo, self.janela = maximo, janela_s
        self._dados = defaultdict(deque)
        self._lock = threading.Lock()

    def permitir(self, chave):
        agora = time.time()
        with self._lock:
            fila = self._dados[chave]
            while fila and fila[0] < agora - self.janela:
                fila.popleft()
            if len(fila) >= self.maximo:
                return False
            fila.append(agora)
            if len(self._dados) > 50000:
                self._dados.clear()
            return True


limite_previa = Limitador(40, 60)
limite_tickets = Limitador(5, 3600)
limite_mensagens = Limitador(30, 600)


def jobs():
    return current_app.extensions['ytd_jobs']


def payload_job(job):
    dados = job.to_dict()
    dados['queue_position'] = jobs().queue_position(job)
    return dados


def plano_de(usuario):
    return 'premium' if db.is_premium(usuario) else 'free'


# ---------------------------------------------------------------------
# Informações
# ---------------------------------------------------------------------

@bp.get('/health')
def health():
    problemas = core.check_dependencies()
    return jsonify({'ok': not problemas, 'problems': problemas, 'ytdlp': core.ytdlp_version()})


@bp.get('/plans')
def planos():
    usuario, _, _ = usuario_api()
    plano = plano_de(usuario)
    free, premium = config.limits_for('free'), config.limits_for('premium')
    return jsonify({
        'current': plano,
        'free': {'channel': 'site', 'max_links': 1, 'playlist': False, 'history': False,
                 'limit_per_hour': config.FREE_LIMIT_PER_HOUR, 'used_this_hour': db.free_usage_count(ip_cliente()),
                 'max_duration_min': free['max_duration_min'], 'audio_kbps': free['audio_kbps'],
                 'video_heights': free['video_heights']},
        'premium': {'channel': 'extensão', 'max_links': config.PREMIUM_MAX_LINKS, 'playlist': True,
                    'max_playlist_items': config.PREMIUM_MAX_PLAYLIST, 'history': True,
                    'daily_limit': config.PREMIUM_DAILY_LIMIT, 'max_duration_min': premium['max_duration_min'],
                    'audio_kbps': premium['audio_kbps'], 'video_heights': premium['video_heights'],
                    'price_brl': config.PREMIUM_PRICE_BRL, 'days': config.PREMIUM_DAYS,
                    'used_24h': db.premium_downloads_last_24h(usuario['id']) if plano == 'premium' else None},
        'formats': {'audio': list(core.AUDIO_FORMATS), 'video': list(core.VIDEO_FORMATS)},
        'avi_max_height': core.AVI_MAX_HEIGHT,
    })


@bp.get('/me')
def me():
    usuario, canal, resposta = usuario_api()
    if resposta:
        return resposta
    if not usuario:
        return erro('Você não está conectado.', 401)
    return jsonify({'email': usuario['email'], 'plan': plano_de(usuario), 'premium_until': usuario['premium_until'],
                    'used_24h': db.premium_downloads_last_24h(usuario['id']),
                    'daily_limit': config.PREMIUM_DAILY_LIMIT, 'limits': config.limits_for(plano_de(usuario))})


@bp.get('/preview')
def previa():
    url = (request.args.get('url') or '').strip()
    if not core.is_youtube_url(url):
        return erro('Link do YouTube inválido.')
    if not limite_previa.permitir(ip_cliente()):
        return erro('Muitas consultas seguidas. Espere um pouco.', 429)
    usuario, _, _ = usuario_api()
    limites = config.limits_for(plano_de(usuario))
    try:
        dados = core.preview_full(url) if request.args.get('full') == '1' else core.preview_quick(url)
    except core.DownloadError as e:
        return erro(str(e), 422)
    dados = dict(dados)
    duracao = dados.get('duration')
    dados['max_duration_min'] = limites['max_duration_min']
    dados['too_long'] = bool(duracao and not dados.get('playlist') and duracao > limites['max_duration_min'] * 60)
    return jsonify(dados)


# ---------------------------------------------------------------------
# Downloads
# ---------------------------------------------------------------------

@bp.post('/jobs')
def criar_job():
    dados = request.get_json(silent=True) or {}
    usuario, canal, resposta = usuario_api()
    if resposta:
        return resposta
    premium = db.is_premium(usuario)
    limites = config.limits_for('premium' if premium else 'free')

    links = dados.get('urls')
    if isinstance(links, str) or links is None:
        links = core.split_links(links or dados.get('url', ''))
    links = list(dict.fromkeys(str(l).strip() for l in links if str(l).strip()))
    if not links:
        return erro('Cole pelo menos um link do YouTube.')
    invalidos = [l for l in links if not core.is_youtube_url(l)]
    if invalidos:
        return erro(f'Este link não é do YouTube: {invalidos[0]}')

    kind = dados.get('kind', 'audio')
    try:
        fmt = core.normalize_format(kind, dados.get('fmt'))
        qualidade = core.normalize_quality(kind, fmt, dados.get('quality'), limites)
    except core.DownloadError as e:
        return erro(str(e), 403 if 'plano' in str(e) else 400, upgrade='plano' in str(e))
    playlist = bool(dados.get('playlist'))

    if premium:
        if len(links) > config.PREMIUM_MAX_LINKS:
            return erro(f'Envie no máximo {config.PREMIUM_MAX_LINKS} links por vez.')
        usados = db.premium_downloads_last_24h(usuario['id'])
        # Playlists são cortadas no que sobra da cota; links avulsos precisam caber inteiros.
        if usados >= config.PREMIUM_DAILY_LIMIT or (not playlist and usados + len(links) > config.PREMIUM_DAILY_LIMIT):
            restante = max(0, config.PREMIUM_DAILY_LIMIT - usados)
            log.info('cota diária do Premium atingida %s', logs.kv(email=usuario['email'], usados=usados))
            return erro(f'Limite de {config.PREMIUM_DAILY_LIMIT} downloads em 24 horas. '
                        f'Restam {restante} agora.', 429)
        job = Job(links, kind, fmt, playlist, quality=qualidade, user=usuario,
                  max_playlist=config.PREMIUM_MAX_PLAYLIST, max_duration_min=limites['max_duration_min'],
                  ip=ip_cliente(), channel=canal)
    else:
        if len(links) > 1:
            return erro('No plano Free é um link por vez. Vários links de uma vez fazem parte do Premium.',
                        403, upgrade=True)
        if playlist or core.is_playlist_url(links[0]):
            return erro('Baixar playlist inteira faz parte do Premium. Para um vídeo só, abra o vídeo e copie o link dele.',
                        403, upgrade=True)
        permitido, espera = db.try_consume_free_slot(ip_cliente(), config.FREE_LIMIT_PER_HOUR)
        if not permitido:
            minutos = max(1, round(espera / 60))
            log.info('limite Free atingido %s', logs.kv(ip=ip_cliente()))
            return erro(f'Você usou os {config.FREE_LIMIT_PER_HOUR} downloads gratuitos desta hora. '
                        f'Libera de novo em {minutos} min, ou assine o Premium.', 429, upgrade=True,
                        retry_after=espera)
        job = Job(links, kind, fmt, playlist=False, quality=qualidade, max_duration_min=limites['max_duration_min'],
                  ip=ip_cliente(), channel=canal)

    jobs().submit(job)
    return jsonify(payload_job(job)), 202


@bp.get('/jobs/<job_id>')
def status_job(job_id):
    job = jobs().get(job_id)
    if not job:
        return erro('Download não encontrado ou já expirado.', 404)
    return jsonify(payload_job(job))


@bp.get('/jobs/<job_id>/file')
def arquivo_job(job_id):
    job = jobs().get(job_id)
    if not job or job.status != 'done' or not job.file_path or not os.path.isfile(job.file_path):
        return erro('Arquivo não disponível. Ele expira alguns minutos depois de pronto.', 404)
    log.info('arquivo entregue %s', logs.kv(job=job.id[:8], arquivo=job.file_name, ip=ip_cliente()))
    return send_file(job.file_path, as_attachment=True, download_name=job.file_name)


# ---------------------------------------------------------------------
# Histórico (Premium)
# ---------------------------------------------------------------------

def exigir_premium():
    usuario, canal, resposta = usuario_api()
    if resposta:
        return None, None, resposta
    if not db.is_premium(usuario):
        return None, None, erro('Recurso exclusivo do Premium.', 401 if not usuario else 403, upgrade=True)
    return usuario, canal, None


@bp.get('/history')
def historico():
    usuario, _, resposta = exigir_premium()
    if resposta:
        return resposta
    try:
        limite = min(200, max(1, int(request.args.get('limit', 50))))
        offset = max(0, int(request.args.get('offset', 0)))
    except ValueError:
        return erro('Parâmetros de paginação inválidos.')
    return jsonify({'items': db.list_history(usuario['id'], request.args.get('q', '').strip(), limite, offset)})


@bp.post('/history/<int:item_id>/redownload')
def baixar_de_novo(item_id):
    usuario, canal, resposta = exigir_premium()
    if resposta:
        return resposta
    item = db.get_history_item(usuario['id'], item_id)
    if not item:
        return erro('Item do histórico não encontrado.', 404)
    if db.premium_downloads_last_24h(usuario['id']) >= config.PREMIUM_DAILY_LIMIT:
        return erro(f'Limite de {config.PREMIUM_DAILY_LIMIT} downloads em 24 horas atingido.', 429)
    dados = request.get_json(silent=True) or {}
    kind = dados.get('kind', item['kind'])
    limites = config.limits_for('premium')
    try:
        fmt = core.normalize_format(kind, dados.get('fmt', item['fmt'] if kind == item['kind'] else None))
        qualidade = core.normalize_quality(kind, fmt, dados.get('quality'), limites)
    except core.DownloadError as e:
        return erro(str(e))
    job = jobs().submit(Job([item['url']], kind, fmt, playlist=False, quality=qualidade, user=usuario,
                            max_duration_min=limites['max_duration_min'], ip=ip_cliente(), channel=canal))
    return jsonify(payload_job(job)), 202


@bp.delete('/history/<int:item_id>')
def apagar_historico(item_id):
    usuario, _, resposta = exigir_premium()
    if resposta:
        return resposta
    if not db.delete_history_item(usuario['id'], item_id):
        return erro('Item do histórico não encontrado.', 404)
    return '', 204


# ---------------------------------------------------------------------
# Suporte (chat do site)
# ---------------------------------------------------------------------

def _ticket_publico(ticket):
    return {'status': ticket['status'], 'messages': db.ticket_messages(ticket['id'])}


@bp.post('/suporte')
def abrir_ticket():
    dados = request.get_json(silent=True) or {}
    mensagem = (dados.get('message') or '').strip()
    usuario = usuario_sessao()
    email = (dados.get('email') or (usuario or {}).get('email') or '').strip().lower()[:200]
    nome = (dados.get('name') or '').strip()[:100]
    if len(mensagem) < 3:
        return erro('Escreva sua mensagem.')
    if len(mensagem) > 4000:
        return erro('Mensagem muito longa (máximo 4000 caracteres).')
    if email and '@' not in email:
        return erro('E-mail inválido.')
    if not limite_tickets.permitir(ip_cliente()):
        return erro('Muitas conversas abertas seguidas. Espere um pouco ou continue a conversa atual.', 429)
    tid, token = db.create_ticket(nome, email, mensagem, user_id=(usuario or {}).get('id'),
                                  page=(dados.get('page') or '')[:300])
    log_sup.info('nova conversa no suporte %s', logs.kv(ticket=tid, email=email, ip=ip_cliente()))
    return jsonify({'token': token, **_ticket_publico(db.get_ticket(tid))}), 201


@bp.get('/suporte/<token>')
def ver_ticket(token):
    ticket = db.get_ticket_by_token(token)
    if not ticket:
        return erro('Conversa não encontrada.', 404)
    nao_lida = bool(ticket['unread_user'])
    if nao_lida and request.args.get('lido') == '1':  # só marca como lida com o chat aberto
        db.mark_ticket_read(ticket['id'], 'user')
    return jsonify({**_ticket_publico(ticket), 'unread': nao_lida})


@bp.post('/suporte/<token>/mensagens')
def responder_ticket(token):
    ticket = db.get_ticket_by_token(token)
    if not ticket:
        return erro('Conversa não encontrada.', 404)
    mensagem = ((request.get_json(silent=True) or {}).get('message') or '').strip()
    if not mensagem or len(mensagem) > 4000:
        return erro('Escreva uma mensagem de até 4000 caracteres.')
    if not limite_mensagens.permitir(ip_cliente()):
        return erro('Muitas mensagens seguidas. Espere um pouco.', 429)
    db.add_ticket_message(ticket['id'], 'user', mensagem)
    log_sup.info('mensagem do cliente %s', logs.kv(ticket=ticket['id']))
    return jsonify(_ticket_publico(db.get_ticket(ticket['id']))), 201


# ---------------------------------------------------------------------
# Webhook do Mercado Pago
# ---------------------------------------------------------------------

@bp.post('/pagamentos/webhook')
def webhook_pagamento():
    corpo = request.get_json(silent=True) or {}
    tipo = corpo.get('type') or corpo.get('topic') or request.args.get('type') or request.args.get('topic')
    data_id = (corpo.get('data') or {}).get('id') or request.args.get('data.id') or request.args.get('id')
    log_pay.info('webhook recebido %s', logs.kv(tipo=tipo, id=data_id, ip=ip_cliente()))
    if tipo != 'payment' or not data_id:
        return jsonify({'ok': True})
    if not payments.verify_webhook_signature(request.headers, data_id):
        log_pay.warning('webhook com assinatura inválida %s', logs.kv(id=data_id, ip=ip_cliente()))
        return erro('assinatura inválida', 401)
    if config.PAYMENT_MODE != 'mercadopago':
        return jsonify({'ok': True})
    try:
        payments.sync_mp_payment(data_id)
    except payments.PaymentError:
        return erro('falha temporária', 503)  # o Mercado Pago tenta de novo depois
    return jsonify({'ok': True})
