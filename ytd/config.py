"""Configuração central lida de variáveis de ambiente (com padrões seguros).

Tudo que muda entre "rodar no meu PC" e "rodar num servidor" fica aqui,
assim o código não precisa ser editado para escalar. Veja .env.example.
"""
import os
import secrets

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _int(nome, padrao):
    try:
        return int(os.environ.get(nome, padrao))
    except ValueError:
        return padrao


def _float(nome, padrao):
    try:
        return float(os.environ.get(nome, padrao))
    except ValueError:
        return padrao


def _bool(nome, padrao):
    return os.environ.get(nome, '1' if padrao else '0').strip().lower() in ('1', 'true', 'sim', 'yes', 'on')


def _lista(nome):
    return [x.strip().lower() for x in os.environ.get(nome, '').split(',') if x.strip()]


# ---------- ambiente ----------
# "development" mostra o link de login na tela quando não há e-mail configurado.
# "production" nunca faz isso (o link vai só para o log).
ENV = os.environ.get('YTD_ENV', 'development').lower()
IS_PRODUCTION = ENV == 'production'

# ---------- pastas ----------
DATA_DIR = os.environ.get('YTD_DATA_DIR', os.path.join(BASE_DIR, 'data'))
JOBS_DIR = os.path.join(DATA_DIR, 'jobs')
LOG_DIR = os.environ.get('YTD_LOG_DIR', os.path.join(DATA_DIR, 'logs'))
DB_PATH = os.environ.get('YTD_DB_PATH', os.path.join(DATA_DIR, 'ytd.sqlite3'))
CLI_DOWNLOAD_DIR = os.environ.get(
    'YTD_CLI_DIR', os.path.join(os.path.expanduser('~'), 'Downloads', 'YT-Downloader'))

# ---------- servidor ----------
HOST = os.environ.get('YTD_HOST', '127.0.0.1')
PORT = _int('YTD_PORT', _int('PORT', 5000))  # PORT é o padrão de Render/Railway/Heroku
# Endereço público (https://seudominio.com.br). Usado em e-mails, pagamento e na extensão.
PUBLIC_URL = os.environ.get('YTD_PUBLIC_URL', '').rstrip('/')
BEHIND_PROXY = _bool('YTD_BEHIND_PROXY', False)
SITE_NAME = os.environ.get('YTD_SITE_NAME', 'YT Downloader')
CONTACT_EMAIL = os.environ.get('YTD_CONTACT_EMAIL', '')
OWNER_NAME = os.environ.get('YTD_OWNER_NAME', 'Victor Hugo')


def _secret_key():
    chave = os.environ.get('YTD_SECRET_KEY')
    if chave:
        return chave
    # Sem variável: gera uma vez e guarda em data/, para as sessões sobreviverem a reinícios.
    caminho = os.path.join(DATA_DIR, 'secret_key')
    os.makedirs(DATA_DIR, exist_ok=True)
    if not os.path.exists(caminho):
        with open(caminho, 'w', encoding='utf-8') as f:
            f.write(secrets.token_hex(32))
    with open(caminho, encoding='utf-8') as f:
        return f.read().strip()


SECRET_KEY = _secret_key()

# ---------- fila de downloads ----------
MAX_WORKERS = _int('YTD_MAX_WORKERS', 2)
FILE_TTL_MIN = _int('YTD_FILE_TTL_MIN', 30)
JOB_TIMEOUT_MIN = _int('YTD_JOB_TIMEOUT_MIN', 45)  # tempo máximo por link antes de cancelar

# ---------- plano Free ----------
FREE_LIMIT_PER_HOUR = _int('YTD_FREE_LIMIT_PER_HOUR', 10)
FREE_MAX_DURATION_MIN = _int('YTD_FREE_MAX_DURATION_MIN', 15)
FREE_AUDIO_KBPS = (128, 192)
FREE_VIDEO_HEIGHTS = (360, 480, 720)

# ---------- plano Premium ----------
PREMIUM_PRICE_BRL = _float('YTD_PREMIUM_PRICE_BRL', 14.90)
PREMIUM_DAYS = _int('YTD_PREMIUM_DAYS', 30)
PREMIUM_DAILY_LIMIT = _int('YTD_PREMIUM_DAILY_LIMIT', 100)
PREMIUM_MAX_DURATION_MIN = _int('YTD_PREMIUM_MAX_DURATION_MIN', 30)
PREMIUM_MAX_LINKS = _int('YTD_PREMIUM_MAX_LINKS', 20)
PREMIUM_MAX_PLAYLIST = _int('YTD_PREMIUM_MAX_PLAYLIST', 100)
PREMIUM_AUDIO_KBPS = (128, 192, 320)
PREMIUM_VIDEO_HEIGHTS = (360, 480, 720, 1080, 1440, 2160)

# ---------- e-mail (login por link mágico) ----------
SMTP_HOST = os.environ.get('YTD_SMTP_HOST', '')
SMTP_PORT = _int('YTD_SMTP_PORT', 587)
SMTP_USER = os.environ.get('YTD_SMTP_USER', '')
SMTP_PASSWORD = os.environ.get('YTD_SMTP_PASSWORD', '')
SMTP_FROM = os.environ.get('YTD_SMTP_FROM', SMTP_USER)
SMTP_SSL = _bool('YTD_SMTP_SSL', False)  # True para porta 465
LOGIN_LINK_TTL_MIN = _int('YTD_LOGIN_LINK_TTL_MIN', 20)
ADMIN_EMAILS = _lista('YTD_ADMIN_EMAILS')  # viram admin automaticamente ao entrar

# ---------- pagamento (Mercado Pago) ----------
MP_ACCESS_TOKEN = os.environ.get('YTD_MP_ACCESS_TOKEN', '')
MP_WEBHOOK_SECRET = os.environ.get('YTD_MP_WEBHOOK_SECRET', '')
# "mercadopago" quando houver token; senão "simulado" (botão de aprovar para testes).
PAYMENT_MODE = os.environ.get('YTD_PAYMENT_MODE', 'mercadopago' if MP_ACCESS_TOKEN else 'simulado')

# ---------- atualização automática do yt-dlp ----------
AUTO_UPDATE = _bool('YTD_AUTO_UPDATE', True)
UPDATE_INTERVAL_H = _float('YTD_UPDATE_INTERVAL_H', 12)

# ---------- logs ----------
LOG_LEVEL = os.environ.get('YTD_LOG_LEVEL', 'INFO').upper()
LOG_MAX_MB = _int('YTD_LOG_MAX_MB', 10)
LOG_RETENTION_DAYS = _int('YTD_LOG_RETENTION_DAYS', 190)


def limits_for(plan):
    """Regras de cada plano num só lugar (usado pela API, páginas e extensão)."""
    if plan == 'premium':
        return {
            'max_duration_min': PREMIUM_MAX_DURATION_MIN,
            'audio_kbps': PREMIUM_AUDIO_KBPS,
            'video_heights': PREMIUM_VIDEO_HEIGHTS,
            'default_kbps': 320,
            'default_height': 1080,
        }
    return {
        'max_duration_min': FREE_MAX_DURATION_MIN,
        'audio_kbps': FREE_AUDIO_KBPS,
        'video_heights': FREE_VIDEO_HEIGHTS,
        'default_kbps': 192,
        'default_height': 720,
    }
