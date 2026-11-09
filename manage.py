"""Administração pelo terminal.

Exemplos:
    python manage.py tornar-admin voce@email.com           # dá acesso ao painel /admin
    python manage.py link-login voce@email.com             # gera link de login (útil sem e-mail configurado)
    python manage.py criar-premium cliente@email.com --dias 30
    python manage.py listar
    python manage.py cancelar cliente@email.com            # volta a conta para Free
    python manage.py atualizar-ytdlp                       # força a verificação de versão agora
"""
import argparse
import sys
from datetime import datetime

from ytd import config, db


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description='Administração do YT Downloader')
    sub = parser.add_subparsers(dest='comando', required=True)
    c = sub.add_parser('criar-premium', help='ativa o Premium e mostra uma chave de acesso nova')
    c.add_argument('email')
    c.add_argument('--dias', type=int, default=None, help='validade em dias (padrão: sem prazo)')
    sub.add_parser('listar', help='lista as contas')
    for nome, ajuda in (('cancelar', 'volta a conta para Free'), ('tornar-admin', 'dá acesso ao painel /admin'),
                        ('remover-admin', 'tira o acesso ao painel'), ('link-login', 'gera um link de login')):
        sub.add_parser(nome, help=ajuda).add_argument('email')
    sub.add_parser('atualizar-ytdlp', help='verifica e instala a versão mais nova do yt-dlp')
    args = parser.parse_args()

    db.init_db()
    if args.comando == 'criar-premium':
        chave = db.create_user(args.email, 'premium', args.dias)
        usuario = db.get_user_by_email(args.email)
        ate = datetime.fromtimestamp(usuario['premium_until']).strftime('%d/%m/%Y') if usuario['premium_until'] else 'sem prazo'
        print(f'Premium ativo para {usuario["email"]} (até {ate}).')
        print(f'Chave de acesso (guarde, ela não é mostrada de novo):\n\n  {chave}\n')
    elif args.comando == 'listar':
        usuarios = db.list_users(limit=10000)
        if not usuarios:
            print('Nenhuma conta cadastrada.')
        for u in usuarios:
            plano = 'premium' if db.is_premium(u) else 'free'
            ate = datetime.fromtimestamp(u['premium_until']).strftime('%d/%m/%Y') if u['premium_until'] else ''
            admin = 'admin' if u['is_admin'] else ''
            print(f"{u['id']:>4}  {plano:<8} {ate:<11} {admin:<6} {u['email']:<40} downloads={u['downloads']}")
    elif args.comando == 'atualizar-ytdlp':
        from ytd import logs, updater
        logs.setup_logging()
        s = updater.check_and_update(motivo='manage.py')
        print(f"Instalada: {s['installed']}  |  Mais nova: {s['latest']}  |  Erro: {s['last_error'] or '-'}")
    elif args.comando == 'link-login':
        db.get_or_create_user(args.email)
        token = db.create_login_token(args.email, '/conta')
        base = config.PUBLIC_URL or f'http://localhost:{config.PORT}'
        print(f'Link de login (vale {config.LOGIN_LINK_TTL_MIN} min, uso único):\n\n  {base}/entrar/{token}\n')
    else:
        usuario = db.get_user_by_email(args.email)
        if args.comando == 'tornar-admin' and not usuario:
            usuario = db.get_or_create_user(args.email)
        if not usuario:
            print(f'Conta {args.email} não encontrada.')
            return
        if args.comando == 'cancelar':
            db.set_plan(args.email, 'free')
            print(f'{args.email} agora está no plano Free.')
        else:
            db.set_admin(usuario['id'], args.comando == 'tornar-admin')
            print(f'{args.email}: {"agora é admin" if args.comando == "tornar-admin" else "não é mais admin"}.')


if __name__ == '__main__':
    main()
