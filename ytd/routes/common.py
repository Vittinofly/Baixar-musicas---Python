"""Utilidades compartilhadas pelas rotas: usuário atual, CSRF, IP, respostas de erro e log de requisições."""
import secrets
import time
from functools import wraps

from flask import abort, g, jsonify, redirect, request, session, url_for

from .. import config, db, logs

log_http = logs.get('http')


def erro(mensagem, status=400, **extra):
    return jsonify({'error': mensagem, **extra}), status


def ip_cliente():
    return request.remote_addr or 'desconhecido'


def base_url():
    return config.PUBLIC_URL or request.host_url.rstrip('/')


# ---------- usuário ----------

def usuario_sessao():
    """Usuário logado no site (cookie de sessão)."""
    if 'usuario_sessao' in g:
        return g.usuario_sessao
    uid = session.get('uid')
    g.usuario_sessao = db.get_user(uid) if uid else None
    if uid and not g.usuario_sessao:
        session.clear()
    return g.usuario_sessao


def chave_enviada():
    auth = request.headers.get('Authorization', '')
    if auth.lower().startswith('bearer '):
        return auth[7:].strip()
    return request.headers.get('X-API-Key', '').strip()


def usuario_api():
    """Identifica quem chama a API: chave (extensão) ou sessão (site).

    Devolve (usuario, canal, resposta_de_erro). Visitante sem login -> (None, 'site', None).
    """
    chave = chave_enviada()
    if chave:
        usuario = db.get_user_by_key(chave)
        if not usuario:
            return None, 'extensao', erro('Chave de acesso inválida. Gere uma nova em Minha conta.', 401)
        if not db.is_premium(usuario):
            return None, 'extensao', erro('Seu Premium não está ativo. Renove em Minha conta.', 403, upgrade=True)
        g.usuario_log = usuario['email']
        return usuario, 'extensao', None
    usuario = usuario_sessao()
    if usuario and request.method not in ('GET', 'HEAD', 'OPTIONS') and not csrf_ok():
        return None, 'site', erro('Sessão expirada. Recarregue a página.', 403)
    if usuario:
        g.usuario_log = usuario['email']
    return usuario, 'site', None


def login_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not usuario_sessao():
            return redirect(url_for('pages.entrar', proximo=request.full_path.rstrip('?')))
        return view(*args, **kwargs)
    return wrapper


def admin_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        usuario = usuario_sessao()
        if not usuario:
            if request.path.startswith('/api/'):
                return erro('Faça login como administrador.', 401)
            return redirect(url_for('pages.entrar', proximo=request.path))
        if not usuario.get('is_admin'):
            if request.path.startswith('/api/'):
                return erro('Acesso restrito a administradores.', 403)
            abort(403)
        if request.method not in ('GET', 'HEAD', 'OPTIONS') and not csrf_ok():
            return erro('Sessão expirada. Recarregue a página.', 403)
        g.usuario_log = usuario['email']
        return view(*args, **kwargs)
    return wrapper


# ---------- CSRF ----------

def csrf_token():
    if 'csrf' not in session:
        session['csrf'] = secrets.token_urlsafe(24)
    return session['csrf']


def csrf_ok():
    enviado = request.headers.get('X-CSRF-Token') or request.form.get('csrf')
    return bool(enviado) and secrets.compare_digest(enviado, session.get('csrf', ''))


# ---------- log de requisições ----------

ROTAS_SILENCIOSAS = ('/static/', '/favicon')


def registrar_hooks(app):
    @app.before_request
    def _inicio():
        g.t0 = time.perf_counter()

    @app.after_request
    def _fim(resposta):
        caminho = request.path
        if caminho.startswith(ROTAS_SILENCIOSAS):
            return resposta
        ms = round((time.perf_counter() - g.get('t0', time.perf_counter())) * 1000)
        campos = logs.kv(ip=ip_cliente(), metodo=request.method, rota=caminho, status=resposta.status_code, ms=ms,
                         usuario=g.get('usuario_log') or (usuario_sessao() or {}).get('email'))
        # Consultas de progresso chegam a cada segundo: ficam em DEBUG para não poluir o log.
        polling = request.method == 'GET' and (caminho.startswith('/api/jobs/') or caminho.startswith('/api/suporte/'))
        if resposta.status_code >= 500:
            log_http.error('requisição %s', campos)
        elif resposta.status_code >= 400 and resposta.status_code != 404:
            log_http.warning('requisição %s', campos)
        elif polling:
            log_http.debug('requisição %s', campos)
        else:
            log_http.info('requisição %s', campos)
        resposta.headers.setdefault('X-Content-Type-Options', 'nosniff')
        resposta.headers.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
        if not caminho.startswith('/api/'):
            resposta.headers.setdefault('X-Frame-Options', 'DENY')
        return resposta

    @app.errorhandler(Exception)
    def _erro(e):
        from werkzeug.exceptions import HTTPException
        if isinstance(e, HTTPException):
            return e
        log_http.exception('erro não tratado %s', logs.kv(rota=request.path, metodo=request.method, ip=ip_cliente()))
        if request.path.startswith('/api/'):
            return erro('Erro interno no servidor. O problema foi registrado.', 500)
        return ('<h1>Erro interno</h1><p>O problema foi registrado. Tente de novo em instantes.</p>', 500)

    @app.context_processor
    def _contexto():
        usuario = usuario_sessao()
        return {
            'usuario': usuario,
            'is_premium': db.is_premium(usuario),
            'csrf': csrf_token(),
            'cfg': config,
            'preco': f'R$ {config.PREMIUM_PRICE_BRL:.2f}'.replace('.', ','),
        }
