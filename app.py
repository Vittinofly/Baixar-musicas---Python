"""Servidor web do YT Downloader (Flask).

Plano Free    -> site: 1 link por vez, sem playlist, até 15 min por vídeo, limite por hora por IP.
Plano Premium -> extensão + conta: vários links, playlists, até 30 min por vídeo, 100 downloads/dia,
                 qualidade máxima, histórico e "baixar de novo".

Rodar localmente:     python app.py            (servidor de produção Waitress)
Modo desenvolvimento: python app.py --debug    (recarrega ao salvar arquivos)
"""
import argparse
from datetime import timedelta

from flask import Flask
from flask_cors import CORS

from ytd import config, core, db, logs, updater
from ytd.jobs import JobManager
from ytd.routes import admin, api, common, pages


def create_app(job_manager=None, background=True):
    logs.setup_logging()
    log = logs.get('app')

    app = Flask(__name__)
    app.json.ensure_ascii = False
    app.config.update(
        SECRET_KEY=config.SECRET_KEY,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE='Lax',
        SESSION_COOKIE_SECURE=config.PUBLIC_URL.startswith('https://'),
        PERMANENT_SESSION_LIFETIME=timedelta(days=30),
        MAX_CONTENT_LENGTH=1 * 1024 * 1024,
        TEMPLATES_AUTO_RELOAD=not config.IS_PRODUCTION,
    )

    if config.BEHIND_PROXY:
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    # A extensão (Premium) chama a API a partir de chrome-extension:// ou moz-extension://.
    CORS(app, resources={r'/api/*': {'origins': [r'^chrome-extension://.*$', r'^moz-extension://.*$']}},
         allow_headers=['Authorization', 'Content-Type', 'X-API-Key'])

    db.init_db()
    app.extensions['ytd_jobs'] = job_manager or JobManager(on_breakage=updater.request_check)
    common.registrar_hooks(app)
    app.register_blueprint(pages.bp)
    app.register_blueprint(api.bp)
    app.register_blueprint(admin.bp)

    if background:
        updater.start_background()
    for problema in core.check_dependencies():
        log.warning('dependência ausente %s', logs.kv(problema=problema))
    log.info('aplicação iniciada %s', logs.kv(ambiente=config.ENV, ytdlp=core.ytdlp_version(),
                                               pagamento=config.PAYMENT_MODE, workers=config.MAX_WORKERS))
    return app


def main():
    parser = argparse.ArgumentParser(description='Servidor web do YT Downloader')
    parser.add_argument('--host', default=config.HOST)
    parser.add_argument('--port', type=int, default=config.PORT)
    parser.add_argument('--debug', action='store_true', help='modo desenvolvimento (Flask com auto-reload)')
    args = parser.parse_args()

    url = f'http://{"localhost" if args.host in ("127.0.0.1", "0.0.0.0") else args.host}:{args.port}'
    if args.debug:
        print(f'Servidor (desenvolvimento) rodando em {url}')
        create_app().run(host=args.host, port=args.port, debug=True)
        return
    from waitress import serve
    print(f'Servidor rodando em {url}  (Ctrl+C para parar)')
    serve(create_app(), host=args.host, port=args.port, threads=16, ident=config.SITE_NAME)


if __name__ == '__main__':
    main()
