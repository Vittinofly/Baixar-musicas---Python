"""Envio de e-mails (login e confirmação de pagamento) por SMTP.

Sem SMTP configurado, o e-mail não é enviado: o conteúdo vai para o log
(e, fora de produção, o link de login aparece na própria tela para testes).
Serviços que funcionam bem: Gmail (senha de app), Brevo, Resend (SMTP), Amazon SES.
"""
import smtplib
import ssl
from email.message import EmailMessage

from . import config, logs

log = logs.get('mailer')


def configured():
    return bool(config.SMTP_HOST and config.SMTP_FROM)


def send(para, assunto, texto, html=None):
    if not configured():
        log.warning('SMTP não configurado; e-mail não enviado %s', logs.kv(para=para, assunto=assunto, texto=texto))
        return False
    msg = EmailMessage()
    msg['From'] = f'{config.SITE_NAME} <{config.SMTP_FROM}>'
    msg['To'] = para
    msg['Subject'] = assunto
    msg.set_content(texto)
    if html:
        msg.add_alternative(html, subtype='html')
    try:
        if config.SMTP_SSL:
            with smtplib.SMTP_SSL(config.SMTP_HOST, config.SMTP_PORT, context=ssl.create_default_context(),
                                  timeout=20) as s:
                if config.SMTP_USER:
                    s.login(config.SMTP_USER, config.SMTP_PASSWORD)
                s.send_message(msg)
        else:
            with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=20) as s:
                s.starttls(context=ssl.create_default_context())
                if config.SMTP_USER:
                    s.login(config.SMTP_USER, config.SMTP_PASSWORD)
                s.send_message(msg)
        log.info('e-mail enviado %s', logs.kv(para=para, assunto=assunto))
        return True
    except Exception as e:
        log.error('falha ao enviar e-mail %s', logs.kv(para=para, assunto=assunto, erro=e))
        return False


def _html(titulo, paragrafo, botao_texto=None, botao_url=None, rodape=''):
    botao = (f'<p style="margin:28px 0"><a href="{botao_url}" style="background:#2846C8;color:#fff;'
             f'padding:12px 22px;border-radius:8px;text-decoration:none;font-weight:700">{botao_texto}</a></p>'
             if botao_url else '')
    return (f'<div style="font-family:Segoe UI,Arial,sans-serif;max-width:520px;margin:auto;color:#1D2433">'
            f'<h2 style="letter-spacing:-.02em">{titulo}</h2><p>{paragrafo}</p>{botao}'
            f'<p style="color:#4A5468;font-size:13px">{rodape}</p></div>')


def send_login_link(email, link):
    texto = (f'Use este link para entrar no {config.SITE_NAME}:\n\n{link}\n\n'
             f'Ele vale por {config.LOGIN_LINK_TTL_MIN} minutos e só funciona uma vez. '
             'Se você não pediu, ignore este e-mail.')
    html = _html('Seu link de acesso', f'Clique no botão para entrar no {config.SITE_NAME}.', 'Entrar', link,
                 f'O link vale por {config.LOGIN_LINK_TTL_MIN} minutos e só funciona uma vez. '
                 'Se você não pediu, ignore este e-mail.')
    return send(email, f'Seu link de acesso ao {config.SITE_NAME}', texto, html)


def send_premium_active(email, ate_texto, conta_url):
    texto = (f'Seu Premium está ativo até {ate_texto}.\n\nAcesse {conta_url} para gerar sua chave de acesso '
             'e instalar a extensão.')
    html = _html('Premium ativado', f'Seu Premium está ativo até <strong>{ate_texto}</strong>. '
                 'Gere sua chave de acesso e instale a extensão na sua conta.', 'Abrir minha conta', conta_url)
    return send(email, 'Seu Premium está ativo', texto, html)


def send_support_reply(email, resposta, url):
    texto = f'O suporte respondeu sua mensagem:\n\n{resposta}\n\nContinue a conversa em {url}'
    return send(email, f'Resposta do suporte — {config.SITE_NAME}', texto)
