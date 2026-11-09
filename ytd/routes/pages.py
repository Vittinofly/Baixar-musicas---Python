"""Páginas do site: início (Free), login por link mágico, Minha conta, pagamento, termos e privacidade."""
import io
import json
import os
import re
import zipfile
from datetime import datetime
from urllib.parse import urlparse

from flask import (Blueprint, abort, flash, redirect, render_template, request, send_file, session,
                   url_for)

from .. import config, db, logs, mailer, payments
from .common import base_url, csrf_ok, ip_cliente, login_required, usuario_sessao

bp = Blueprint('pages', __name__)
log = logs.get('auth')
EXTENSION_DIR = os.path.join(config.BASE_DIR, 'extension')
EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')


def data_br(ts):
    return datetime.fromtimestamp(ts).strftime('%d/%m/%Y') if ts else ''


def proximo_seguro(url):
    """Evita redirecionamento para outro site depois do login."""
    if url and url.startswith('/') and not url.startswith('//') and not urlparse(url).netloc:
        return url
    return url_for('pages.conta')


@bp.app_template_filter('data')
def _filtro_data(ts):
    return data_br(ts)


@bp.app_template_filter('datahora')
def _filtro_datahora(ts):
    return datetime.fromtimestamp(ts).strftime('%d/%m/%Y %H:%M') if ts else ''


# ---------------------------------------------------------------------

@bp.get('/')
def index():
    return render_template('index.html')


@bp.get('/termos')
def termos():
    return render_template('termos.html', atualizado='07/11/2026')


@bp.get('/privacidade')
def privacidade():
    return render_template('privacidade.html', atualizado='07/11/2026')


# ---------- login ----------

@bp.route('/entrar', methods=['GET', 'POST'])
def entrar():
    proximo = request.values.get('proximo') or ''
    if usuario_sessao() and request.method == 'GET':
        return redirect(proximo_seguro(proximo))
    if request.method == 'GET':
        return render_template('entrar.html', proximo=proximo)

    if not csrf_ok():
        flash('Sessão expirada. Tente de novo.', 'erro')
        return redirect(url_for('pages.entrar', proximo=proximo))
    email = (request.form.get('email') or '').strip().lower()
    if not EMAIL_RE.match(email) or len(email) > 200:
        flash('Digite um e-mail válido.', 'erro')
        return render_template('entrar.html', proximo=proximo, email=email), 400
    if db.recent_login_requests(email) >= 3:
        log.warning('muitos pedidos de login %s', logs.kv(email=email, ip=ip_cliente()))
        flash('Já enviamos alguns links para esse e-mail. Confira a caixa de entrada e o spam, '
              'ou espere 10 minutos para pedir outro.', 'erro')
        return render_template('entrar.html', proximo=proximo, email=email), 429

    token = db.create_login_token(email, proximo_seguro(proximo), ip_cliente())
    link = f'{base_url()}{url_for("pages.entrar_token", token=token)}'
    enviado = mailer.send_login_link(email, link)
    log.info('link de login gerado %s', logs.kv(email=email, ip=ip_cliente(), enviado_por_email=enviado))
    link_teste = None
    if not enviado:
        if config.IS_PRODUCTION:
            log.error('SMTP não configurado em produção: cliente não recebe o link %s', logs.kv(email=email))
        else:
            link_teste = link  # só em desenvolvimento, para testar sem e-mail
    return render_template('entrar_enviado.html', email=email, link_teste=link_teste)


@bp.get('/entrar/<token>')
def entrar_token(token):
    resultado = db.consume_login_token(token)
    if not resultado:
        log.info('link de login inválido ou expirado %s', logs.kv(ip=ip_cliente()))
        flash('Esse link expirou ou já foi usado. Peça um novo abaixo.', 'erro')
        return redirect(url_for('pages.entrar'))
    email, proximo = resultado
    usuario = db.get_or_create_user(email)
    if email in config.ADMIN_EMAILS and not usuario['is_admin']:
        db.set_admin(usuario['id'], True)
    db.touch_login(usuario['id'])
    session.clear()
    session.permanent = True
    session['uid'] = usuario['id']
    log.info('login %s', logs.kv(email=email, ip=ip_cliente()))
    return redirect(proximo_seguro(proximo))


@bp.post('/sair')
def sair():
    if csrf_ok():
        usuario = usuario_sessao()
        if usuario:
            log.info('logout %s', logs.kv(email=usuario['email']))
        session.clear()
    return redirect(url_for('pages.index'))


# ---------- conta ----------

@bp.get('/conta')
@login_required
def conta():
    usuario = usuario_sessao()
    premium = db.is_premium(usuario)
    return render_template(
        'conta.html',
        premium=premium,
        usados_24h=db.premium_downloads_last_24h(usuario['id']) if premium else 0,
        pagamentos=db.list_payments(usuario['id'], limit=20),
        nova_chave=session.pop('nova_chave', None),
        historico=db.list_history(usuario['id'], limit=10) if premium else [],
    )


@bp.post('/conta/chave')
@login_required
def gerar_chave():
    usuario = usuario_sessao()
    if not csrf_ok():
        abort(403)
    if not db.is_premium(usuario):
        flash('A chave de acesso é do Premium. Assine para liberar a extensão.', 'erro')
        return redirect(url_for('pages.conta'))
    session['nova_chave'] = db.regenerate_key(usuario['id'])
    log.info('chave de acesso gerada %s', logs.kv(email=usuario['email']))
    return redirect(url_for('pages.conta') + '#chave')


# ---------- pagamento ----------

@bp.post('/assinar')
@login_required
def assinar():
    if not csrf_ok():
        abort(403)
    try:
        url = payments.start_checkout(usuario_sessao(), base_url())
    except payments.PaymentError as e:
        flash(str(e), 'erro')
        return redirect(url_for('pages.conta'))
    return redirect(url)


@bp.route('/pagamento/simulado/<int:pid>', methods=['GET', 'POST'])
@login_required
def pagamento_simulado(pid):
    pagamento = db.get_payment(pid)
    if config.PAYMENT_MODE == 'mercadopago' or not pagamento or pagamento['user_id'] != usuario_sessao()['id']:
        abort(404)
    if request.method == 'POST':
        if not csrf_ok():
            abort(403)
        payments.approve_simulated(pid)
        flash('Pagamento aprovado (simulação). Seu Premium está ativo.', 'ok')
        return redirect(url_for('pages.conta'))
    return render_template('pagamento_simulado.html', pagamento=pagamento)


@bp.get('/pagamento/retorno')
@login_required
def pagamento_retorno():
    mp_id = request.args.get('payment_id') or request.args.get('collection_id')
    status = request.args.get('status') or request.args.get('collection_status')
    if mp_id and mp_id != 'null' and config.PAYMENT_MODE == 'mercadopago':
        try:
            pagamento = payments.sync_mp_payment(mp_id)
            status = (pagamento or {}).get('status', status)
        except payments.PaymentError as e:
            flash(str(e), 'erro')
            return redirect(url_for('pages.conta'))
    mensagens = {
        'approved': ('Pagamento aprovado. Seu Premium está ativo.', 'ok'),
        'pending': ('Pagamento em processamento. Com Pix, a confirmação costuma levar segundos; '
                    'atualize esta página em instantes.', 'info'),
        'in_process': ('Pagamento em análise. Avisamos por e-mail quando for aprovado.', 'info'),
    }
    texto, tipo = mensagens.get(status, ('O pagamento não foi concluído. Você pode tentar de novo.', 'erro'))
    flash(texto, tipo)
    return redirect(url_for('pages.conta'))


# ---------- extensão ----------

@bp.get('/extensao.zip')
@bp.get('/extensao/<navegador>.zip')
def extensao_zip(navegador='chrome'):
    """Empacota a extensão com o endereço deste servidor já configurado."""
    if navegador not in ('chrome', 'firefox') or not os.path.isdir(EXTENSION_DIR):
        abort(404)
    servidor = base_url()
    origem = f'{urlparse(servidor).scheme}://{urlparse(servidor).netloc}/*'
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
        for raiz, _, arquivos in os.walk(EXTENSION_DIR):
            for nome in arquivos:
                caminho = os.path.join(raiz, nome)
                relativo = os.path.relpath(caminho, EXTENSION_DIR).replace(os.sep, '/')
                if relativo == 'manifest.json':
                    with open(caminho, encoding='utf-8') as f:
                        manifesto = json.load(f)
                    if origem not in manifesto['host_permissions']:
                        manifesto['host_permissions'].append(origem)
                    if navegador == 'chrome':
                        manifesto.pop('browser_specific_settings', None)
                        manifesto['background'] = {'service_worker': 'background.js'}
                    else:
                        manifesto['background'] = {'scripts': ['api.js', 'background.js']}
                    zf.writestr(relativo, json.dumps(manifesto, ensure_ascii=False, indent=2))
                elif relativo == 'api.js':
                    with open(caminho, encoding='utf-8') as f:
                        texto = f.read()
                    texto = texto.replace("server: 'http://localhost:5000'", f"server: {json.dumps(servidor)}")
                    zf.writestr(relativo, texto)
                else:
                    zf.write(caminho, relativo)
    buffer.seek(0)
    log.info('extensão baixada %s', logs.kv(navegador=navegador, ip=ip_cliente()))
    return send_file(buffer, mimetype='application/zip', as_attachment=True,
                     download_name=f'yt-downloader-{navegador}.zip')
