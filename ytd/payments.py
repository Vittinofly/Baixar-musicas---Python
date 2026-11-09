"""Pagamento do Premium.

Modo "mercadopago": cria um Checkout Pro (Pix, cartão, boleto) e confirma pelo webhook
                    e pela volta do cliente ao site (o que chegar primeiro).
Modo "simulado":    para testes locais; mostra um botão "Aprovar pagamento" em vez de cobrar.

Cada pagamento aprovado soma YTD_PREMIUM_DAYS dias ao Premium (sem renovação automática).
"""
import hashlib
import hmac
import json
import urllib.request

from . import config, db, logs

log = logs.get('payments')
API = 'https://api.mercadopago.com'


class PaymentError(Exception):
    pass


def _mp(metodo, caminho, corpo=None):
    dados = json.dumps(corpo).encode() if corpo is not None else None
    req = urllib.request.Request(API + caminho, data=dados, method=metodo, headers={
        'Authorization': f'Bearer {config.MP_ACCESS_TOKEN}', 'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        corpo_erro = e.read().decode(errors='replace')[:500]
        log.error('erro na API do Mercado Pago %s', logs.kv(caminho=caminho, status=e.code, resposta=corpo_erro))
        raise PaymentError('Não foi possível falar com o Mercado Pago agora. Tente de novo em instantes.') from e
    except Exception as e:
        log.error('falha de conexão com o Mercado Pago %s', logs.kv(caminho=caminho, erro=e))
        raise PaymentError('Não foi possível falar com o Mercado Pago agora. Tente de novo em instantes.') from e


def start_checkout(user, base_url):
    """Cria o pagamento e devolve a URL para onde o cliente deve ir."""
    pid = db.create_payment(user['id'], config.PAYMENT_MODE, config.PREMIUM_PRICE_BRL, config.PREMIUM_DAYS)
    log.info('checkout iniciado %s', logs.kv(pagamento=pid, usuario=user['email'], modo=config.PAYMENT_MODE,
                                             valor=config.PREMIUM_PRICE_BRL))
    if config.PAYMENT_MODE != 'mercadopago':
        url = f'{base_url}/pagamento/simulado/{pid}'
        db.update_payment(pid, checkout_url=url)
        return url

    preferencia = _mp('POST', '/checkout/preferences', {
        'items': [{'id': 'premium-30d', 'title': f'{config.SITE_NAME} Premium — {config.PREMIUM_DAYS} dias',
                   'quantity': 1, 'unit_price': round(config.PREMIUM_PRICE_BRL, 2), 'currency_id': 'BRL'}],
        'payer': {'email': user['email']},
        'external_reference': str(pid),
        'notification_url': f'{base_url}/api/pagamentos/webhook',
        'back_urls': {k: f'{base_url}/pagamento/retorno' for k in ('success', 'failure', 'pending')},
        'auto_return': 'approved',
        'statement_descriptor': config.SITE_NAME[:13],
    })
    url = preferencia.get('init_point')
    db.update_payment(pid, external_id=preferencia.get('id'), checkout_url=url)
    return url


def sync_mp_payment(mp_payment_id):
    """Consulta um pagamento no Mercado Pago e aplica o resultado. Devolve o pagamento local."""
    p = _mp('GET', f'/v1/payments/{mp_payment_id}')
    try:
        pid = int(p.get('external_reference') or 0)
    except ValueError:
        pid = 0
    local = db.get_payment(pid)
    if not local:
        log.warning('pagamento do Mercado Pago sem referência local %s', logs.kv(mp=mp_payment_id))
        return None
    status = p.get('status')
    valor = float(p.get('transaction_amount') or 0)
    detalhe = f"{p.get('payment_method_id')} {p.get('status_detail')}"
    if status == 'approved':
        if valor + 0.01 < local['amount'] or p.get('currency_id') != 'BRL':
            log.error('valor pago diferente do esperado %s', logs.kv(pagamento=pid, pago=valor, esperado=local['amount']))
            db.update_payment(pid, status='review', detail=f'valor {valor} diferente de {local["amount"]}')
            return db.get_payment(pid)
        antes = local['status']
        local = db.approve_payment(pid, external_id=str(mp_payment_id), detail=detalhe)
        if antes != 'approved':
            log.info('pagamento aprovado %s', logs.kv(pagamento=pid, mp=mp_payment_id, valor=valor))
            _avisar_ativacao(local)
    elif status in ('rejected', 'cancelled', 'refunded', 'charged_back'):
        db.update_payment(pid, status=status, external_id=str(mp_payment_id), detail=detalhe)
        log.info('pagamento não aprovado %s', logs.kv(pagamento=pid, status=status, detalhe=detalhe))
        if status in ('refunded', 'charged_back') and local['status'] == 'approved':
            log.warning('pagamento estornado: revise o Premium do cliente %s', logs.kv(pagamento=pid))
    else:
        db.update_payment(pid, status='pending', external_id=str(mp_payment_id), detail=detalhe)
    return db.get_payment(pid)


def approve_simulated(pid):
    if config.PAYMENT_MODE == 'mercadopago':
        raise PaymentError('Pagamento simulado desativado.')
    antes = (db.get_payment(pid) or {}).get('status')
    pagamento = db.approve_payment(pid, external_id=f'sim-{pid}', detail='pagamento simulado')
    if pagamento and antes != 'approved':
        log.info('pagamento simulado aprovado %s', logs.kv(pagamento=pid))
        _avisar_ativacao(pagamento)
    return pagamento


def _avisar_ativacao(pagamento):
    from datetime import datetime
    from . import mailer
    user = db.get_user(pagamento['user_id'])
    ate = user.get('premium_until')
    ate_texto = datetime.fromtimestamp(ate).strftime('%d/%m/%Y') if ate else 'sem prazo'
    base = config.PUBLIC_URL or f'http://localhost:{config.PORT}'
    mailer.send_premium_active(user['email'], ate_texto, f'{base}/conta')


def verify_webhook_signature(headers, data_id):
    """Valida o cabeçalho x-signature do Mercado Pago (quando YTD_MP_WEBHOOK_SECRET está definido)."""
    if not config.MP_WEBHOOK_SECRET:
        return True
    assinatura = headers.get('x-signature', '')
    partes = dict(p.split('=', 1) for p in assinatura.split(',') if '=' in p)
    ts, v1 = partes.get('ts', '').strip(), partes.get('v1', '').strip()
    manifesto = f'id:{str(data_id).lower()};request-id:{headers.get("x-request-id", "")};ts:{ts};'
    esperado = hmac.new(config.MP_WEBHOOK_SECRET.encode(), manifesto.encode(), hashlib.sha256).hexdigest()
    return bool(v1) and hmac.compare_digest(esperado, v1)
