"""Persistência em SQLite.

Tabelas:
  users          contas (Free/Premium, validade, admin) — a chave de acesso é guardada só como hash
  login_tokens   links mágicos de login enviados por e-mail
  payments       pagamentos (Mercado Pago ou simulados)
  downloads      registro de TODOS os downloads (Free e Premium) para estatística e auditoria
  history        histórico visível ao cliente Premium (ele pode apagar itens)
  free_usage     contador do limite por hora do plano Free
  tickets / ticket_messages   conversas do suporte
  settings       pequenos valores do sistema (ex.: estado da atualização do yt-dlp)

SQLite com WAL aguenta bem um servidor. Para várias máquinas, migre para PostgreSQL
mantendo estas funções.
"""
import hashlib
import os
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager

from . import config

_lock = threading.Lock()
DIA = 86400

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL UNIQUE,
    plan TEXT NOT NULL DEFAULT 'free',
    api_key_hash TEXT NOT NULL UNIQUE,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    url TEXT NOT NULL,
    title TEXT NOT NULL,
    kind TEXT NOT NULL,
    fmt TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_history_user ON history(user_id, created_at DESC);
CREATE TABLE IF NOT EXISTS free_usage (ip TEXT NOT NULL, created_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS idx_free_usage ON free_usage(ip, created_at);

CREATE TABLE IF NOT EXISTS login_tokens (
    token_hash TEXT PRIMARY KEY,
    email TEXT NOT NULL,
    next_url TEXT,
    ip TEXT,
    expires_at REAL NOT NULL,
    used_at REAL
);
CREATE TABLE IF NOT EXISTS payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    provider TEXT NOT NULL,
    external_id TEXT,
    status TEXT NOT NULL,
    amount REAL NOT NULL,
    days INTEGER NOT NULL,
    checkout_url TEXT,
    detail TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_payments_user ON payments(user_id, created_at DESC);
CREATE TABLE IF NOT EXISTS downloads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL,
    user_id INTEGER,
    ip TEXT,
    plan TEXT NOT NULL,
    channel TEXT,
    url TEXT NOT NULL,
    title TEXT,
    kind TEXT,
    fmt TEXT,
    quality TEXT,
    status TEXT NOT NULL,
    error TEXT,
    media_duration REAL,
    size_bytes INTEGER,
    elapsed REAL,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_downloads_time ON downloads(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_downloads_user ON downloads(user_id, created_at DESC);
CREATE TABLE IF NOT EXISTS tickets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token_hash TEXT NOT NULL UNIQUE,
    user_id INTEGER,
    name TEXT,
    email TEXT,
    status TEXT NOT NULL DEFAULT 'open',
    unread_admin INTEGER NOT NULL DEFAULT 1,
    unread_user INTEGER NOT NULL DEFAULT 0,
    page TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS ticket_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id INTEGER NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
    sender TEXT NOT NULL,
    body TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ticket_messages ON ticket_messages(ticket_id, id);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
"""

# Colunas adicionadas depois da primeira versão (migração automática).
MIGRATIONS = {
    'users': {
        'premium_until': 'REAL',
        'is_admin': 'INTEGER NOT NULL DEFAULT 0',
        'last_login': 'REAL',
    },
}


@contextmanager
def connect():
    os.makedirs(os.path.dirname(config.DB_PATH) or '.', exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with connect() as conn:
        conn.execute('PRAGMA journal_mode = WAL')
        conn.executescript(SCHEMA)
        for tabela, colunas in MIGRATIONS.items():
            existentes = {r['name'] for r in conn.execute(f'PRAGMA table_info({tabela})')}
            for nome, tipo in colunas.items():
                if nome not in existentes:
                    conn.execute(f'ALTER TABLE {tabela} ADD COLUMN {nome} {tipo}')


def hash_token(valor):
    return hashlib.sha256(valor.encode('utf-8')).hexdigest()


hash_key = hash_token


def _rows(cur):
    return [dict(r) for r in cur]


# =====================================================================
# Contas
# =====================================================================

USER_COLS = 'id, email, plan, premium_until, is_admin, created_at, last_login'


def is_premium(user):
    if not user or user.get('plan') != 'premium':
        return False
    ate = user.get('premium_until')
    return ate is None or ate > time.time()


def get_user(user_id):
    with connect() as conn:
        row = conn.execute(f'SELECT {USER_COLS} FROM users WHERE id = ?', (user_id,)).fetchone()
    return dict(row) if row else None


def get_user_by_email(email):
    with connect() as conn:
        row = conn.execute(f'SELECT {USER_COLS} FROM users WHERE email = ?', (email.strip().lower(),)).fetchone()
    return dict(row) if row else None


def get_or_create_user(email):
    email = email.strip().lower()
    with connect() as conn:
        row = conn.execute(f'SELECT {USER_COLS} FROM users WHERE email = ?', (email,)).fetchone()
        if row:
            return dict(row)
        # Toda conta tem uma chave aleatória (nunca mostrada) até o cliente gerar a dele.
        conn.execute('INSERT INTO users (email, plan, api_key_hash, created_at, is_admin) VALUES (?, ?, ?, ?, ?)',
                     (email, 'free', hash_token(secrets.token_hex(32)), time.time(),
                      1 if email in config.ADMIN_EMAILS else 0))
    return get_user_by_email(email)


def regenerate_key(user_id):
    """Gera uma nova chave de acesso (a anterior para de funcionar) e devolve em texto puro."""
    api_key = 'ytd_' + secrets.token_urlsafe(32)
    with connect() as conn:
        conn.execute('UPDATE users SET api_key_hash = ? WHERE id = ?', (hash_token(api_key), user_id))
    return api_key


def create_user(email, plan='premium', days=None):
    """Cria/atualiza conta e devolve uma chave nova (usado por manage.py e testes)."""
    user = get_or_create_user(email)
    if plan == 'premium':
        grant_premium(user['id'], days)
    else:
        set_plan(email, plan)
    return regenerate_key(user['id'])


def grant_premium(user_id, days=None):
    """Ativa o Premium. days=None = sem prazo; senão soma dias à validade atual."""
    with connect() as conn:
        row = conn.execute('SELECT plan, premium_until FROM users WHERE id = ?', (user_id,)).fetchone()
        if days is None:
            ate = None
        else:
            base = time.time()
            if row and row['plan'] == 'premium' and row['premium_until'] and row['premium_until'] > base:
                base = row['premium_until']
            ate = base + days * DIA
        conn.execute("UPDATE users SET plan = 'premium', premium_until = ? WHERE id = ?", (ate, user_id))
    return ate


def get_user_by_key(api_key):
    if not api_key:
        return None
    with connect() as conn:
        row = conn.execute(f'SELECT {USER_COLS} FROM users WHERE api_key_hash = ?', (hash_token(api_key),)).fetchone()
    return dict(row) if row else None


def touch_login(user_id):
    with connect() as conn:
        conn.execute('UPDATE users SET last_login = ? WHERE id = ?', (time.time(), user_id))


def set_plan(email, plan):
    with connect() as conn:
        if plan == 'free':
            cur = conn.execute("UPDATE users SET plan = 'free', premium_until = NULL WHERE email = ?",
                               (email.strip().lower(),))
        else:
            cur = conn.execute("UPDATE users SET plan = ? WHERE email = ?", (plan, email.strip().lower()))
        return cur.rowcount > 0


def set_admin(user_id, flag):
    with connect() as conn:
        conn.execute('UPDATE users SET is_admin = ? WHERE id = ?', (1 if flag else 0, user_id))


def list_users(query='', limit=100, offset=0):
    sql = f'''SELECT {', '.join('u.' + c for c in USER_COLS.split(', '))},
                (SELECT COUNT(*) FROM downloads d WHERE d.user_id = u.id AND d.status = 'done') AS downloads,
                (SELECT COALESCE(SUM(amount), 0) FROM payments p WHERE p.user_id = u.id AND p.status = 'approved') AS paid
              FROM users u'''
    params = []
    if query:
        sql += ' WHERE u.email LIKE ?'
        params.append(f'%{query.lower()}%')
    sql += ' ORDER BY u.id DESC LIMIT ? OFFSET ?'
    with connect() as conn:
        return _rows(conn.execute(sql, params + [limit, offset]))


# =====================================================================
# Login por link mágico
# =====================================================================

def create_login_token(email, next_url=None, ip=None):
    token = secrets.token_urlsafe(32)
    with connect() as conn:
        conn.execute('DELETE FROM login_tokens WHERE expires_at < ?', (time.time() - DIA,))
        conn.execute('INSERT INTO login_tokens (token_hash, email, next_url, ip, expires_at) VALUES (?, ?, ?, ?, ?)',
                     (hash_token(token), email.strip().lower(), next_url, ip,
                      time.time() + config.LOGIN_LINK_TTL_MIN * 60))
    return token


def consume_login_token(token):
    """Valida e "queima" o token. Devolve (email, next_url) ou None."""
    with _lock, connect() as conn:
        row = conn.execute('SELECT email, next_url, expires_at, used_at FROM login_tokens WHERE token_hash = ?',
                           (hash_token(token),)).fetchone()
        if not row or row['used_at'] or row['expires_at'] < time.time():
            return None
        conn.execute('UPDATE login_tokens SET used_at = ? WHERE token_hash = ?', (time.time(), hash_token(token)))
    return row['email'], row['next_url']


def recent_login_requests(email, minutos=10):
    with connect() as conn:
        return conn.execute('SELECT COUNT(*) AS n FROM login_tokens WHERE email = ? AND expires_at > ?',
                            (email.strip().lower(), time.time() + (config.LOGIN_LINK_TTL_MIN - minutos) * 60)
                            ).fetchone()['n']


# =====================================================================
# Pagamentos
# =====================================================================

def create_payment(user_id, provider, amount, days):
    agora = time.time()
    with connect() as conn:
        cur = conn.execute('INSERT INTO payments (user_id, provider, status, amount, days, created_at, updated_at) '
                           'VALUES (?, ?, ?, ?, ?, ?, ?)', (user_id, provider, 'pending', amount, days, agora, agora))
        return cur.lastrowid


def update_payment(payment_id, **campos):
    campos['updated_at'] = time.time()
    sets = ', '.join(f'{k} = ?' for k in campos)
    with connect() as conn:
        conn.execute(f'UPDATE payments SET {sets} WHERE id = ?', (*campos.values(), payment_id))


def get_payment(payment_id):
    with connect() as conn:
        row = conn.execute('SELECT * FROM payments WHERE id = ?', (payment_id,)).fetchone()
    return dict(row) if row else None


def approve_payment(payment_id, external_id=None, detail=None):
    """Marca como aprovado e ativa o Premium uma única vez (idempotente para webhooks repetidos)."""
    with _lock:
        pagamento = get_payment(payment_id)
        if not pagamento:
            return None
        if pagamento['status'] == 'approved':
            return pagamento
        update_payment(payment_id, status='approved', external_id=external_id or pagamento['external_id'],
                       detail=detail)
        grant_premium(pagamento['user_id'], pagamento['days'])
    return get_payment(payment_id)


def list_payments(user_id=None, limit=100):
    sql = 'SELECT p.*, u.email FROM payments p JOIN users u ON u.id = p.user_id'
    params = []
    if user_id:
        sql += ' WHERE p.user_id = ?'
        params.append(user_id)
    sql += ' ORDER BY p.created_at DESC LIMIT ?'
    with connect() as conn:
        return _rows(conn.execute(sql, params + [limit]))


# =====================================================================
# Downloads (registro completo) e histórico Premium
# =====================================================================

def log_download(**campos):
    campos.setdefault('created_at', time.time())
    cols = ', '.join(campos)
    marks = ', '.join('?' for _ in campos)
    with connect() as conn:
        conn.execute(f'INSERT INTO downloads ({cols}) VALUES ({marks})', tuple(campos.values()))


def premium_downloads_last_24h(user_id):
    with connect() as conn:
        return conn.execute("SELECT COUNT(*) AS n FROM downloads WHERE user_id = ? AND status = 'done' "
                            "AND created_at >= ?", (user_id, time.time() - DIA)).fetchone()['n']


def list_downloads(status=None, plan=None, query='', limit=200, offset=0):
    sql = 'SELECT d.*, u.email FROM downloads d LEFT JOIN users u ON u.id = d.user_id WHERE 1=1'
    params = []
    if status:
        sql += ' AND d.status = ?'
        params.append(status)
    if plan:
        sql += ' AND d.plan = ?'
        params.append(plan)
    if query:
        sql += ' AND (d.title LIKE ? OR d.url LIKE ? OR u.email LIKE ? OR d.ip LIKE ?)'
        params += [f'%{query}%'] * 4
    sql += ' ORDER BY d.created_at DESC LIMIT ? OFFSET ?'
    with connect() as conn:
        return _rows(conn.execute(sql, params + [limit, offset]))


def add_history(user_id, url, title, kind, fmt):
    with connect() as conn:
        return conn.execute('INSERT INTO history (user_id, url, title, kind, fmt, created_at) VALUES (?, ?, ?, ?, ?, ?)',
                            (user_id, url, title, kind, fmt, time.time())).lastrowid


def list_history(user_id, query='', limit=50, offset=0):
    sql = 'SELECT id, url, title, kind, fmt, created_at FROM history WHERE user_id = ?'
    params = [user_id]
    if query:
        sql += ' AND title LIKE ?'
        params.append(f'%{query}%')
    sql += ' ORDER BY created_at DESC LIMIT ? OFFSET ?'
    with connect() as conn:
        return _rows(conn.execute(sql, params + [limit, offset]))


def get_history_item(user_id, item_id):
    with connect() as conn:
        row = conn.execute('SELECT id, url, title, kind, fmt, created_at FROM history WHERE id = ? AND user_id = ?',
                           (item_id, user_id)).fetchone()
    return dict(row) if row else None


def delete_history_item(user_id, item_id):
    with connect() as conn:
        return conn.execute('DELETE FROM history WHERE id = ? AND user_id = ?', (item_id, user_id)).rowcount > 0


# =====================================================================
# Limite do plano Free
# =====================================================================

def try_consume_free_slot(ip, limit_per_hour):
    """Registra um download Free para o IP. Devolve (permitido, segundos_para_liberar)."""
    agora = time.time()
    with _lock, connect() as conn:
        conn.execute('DELETE FROM free_usage WHERE created_at < ?', (agora - 3600,))
        usos = conn.execute('SELECT created_at FROM free_usage WHERE ip = ? ORDER BY created_at', (ip,)).fetchall()
        if len(usos) >= limit_per_hour:
            return False, int(usos[0]['created_at'] + 3600 - agora) + 1
        conn.execute('INSERT INTO free_usage (ip, created_at) VALUES (?, ?)', (ip, agora))
    return True, 0


def refund_free_slot(ip):
    """Devolve o crédito quando o download falha por motivo que não é culpa do usuário."""
    with _lock, connect() as conn:
        conn.execute('DELETE FROM free_usage WHERE rowid = (SELECT rowid FROM free_usage WHERE ip = ? '
                     'ORDER BY created_at DESC LIMIT 1)', (ip,))


def free_usage_count(ip):
    with connect() as conn:
        return conn.execute('SELECT COUNT(*) AS n FROM free_usage WHERE ip = ? AND created_at >= ?',
                            (ip, time.time() - 3600)).fetchone()['n']


# =====================================================================
# Suporte
# =====================================================================

def create_ticket(name, email, body, user_id=None, page=None):
    token = secrets.token_urlsafe(24)
    agora = time.time()
    with connect() as conn:
        tid = conn.execute('INSERT INTO tickets (token_hash, user_id, name, email, page, created_at, updated_at) '
                           'VALUES (?, ?, ?, ?, ?, ?, ?)',
                           (hash_token(token), user_id, name, email, page, agora, agora)).lastrowid
        conn.execute('INSERT INTO ticket_messages (ticket_id, sender, body, created_at) VALUES (?, ?, ?, ?)',
                     (tid, 'user', body, agora))
    return tid, token


def get_ticket_by_token(token):
    with connect() as conn:
        row = conn.execute('SELECT * FROM tickets WHERE token_hash = ?', (hash_token(token),)).fetchone()
    return dict(row) if row else None


def get_ticket(ticket_id):
    with connect() as conn:
        row = conn.execute('SELECT * FROM tickets WHERE id = ?', (ticket_id,)).fetchone()
    return dict(row) if row else None


def ticket_messages(ticket_id):
    with connect() as conn:
        return _rows(conn.execute('SELECT id, sender, body, created_at FROM ticket_messages WHERE ticket_id = ? '
                                  'ORDER BY id', (ticket_id,)))


def add_ticket_message(ticket_id, sender, body):
    agora = time.time()
    with connect() as conn:
        conn.execute('INSERT INTO ticket_messages (ticket_id, sender, body, created_at) VALUES (?, ?, ?, ?)',
                     (ticket_id, sender, body, agora))
        if sender == 'user':
            conn.execute("UPDATE tickets SET updated_at = ?, unread_admin = 1, status = 'open' WHERE id = ?",
                         (agora, ticket_id))
        else:
            conn.execute('UPDATE tickets SET updated_at = ?, unread_user = 1, unread_admin = 0 WHERE id = ?',
                         (agora, ticket_id))


def mark_ticket_read(ticket_id, por):
    campo = 'unread_admin' if por == 'admin' else 'unread_user'
    with connect() as conn:
        conn.execute(f'UPDATE tickets SET {campo} = 0 WHERE id = ?', (ticket_id,))


def set_ticket_status(ticket_id, status):
    with connect() as conn:
        conn.execute('UPDATE tickets SET status = ?, updated_at = ? WHERE id = ?', (status, time.time(), ticket_id))


def list_tickets(status=None, limit=200):
    sql = '''SELECT t.*, (SELECT body FROM ticket_messages m WHERE m.ticket_id = t.id ORDER BY id DESC LIMIT 1) AS last,
                    (SELECT COUNT(*) FROM ticket_messages m WHERE m.ticket_id = t.id) AS total
             FROM tickets t'''
    params = []
    if status:
        sql += ' WHERE t.status = ?'
        params.append(status)
    sql += ' ORDER BY t.unread_admin DESC, t.updated_at DESC LIMIT ?'
    with connect() as conn:
        return _rows(conn.execute(sql, params + [limit]))


# =====================================================================
# Configurações e estatísticas
# =====================================================================

def get_setting(key, default=None):
    with connect() as conn:
        row = conn.execute('SELECT value FROM settings WHERE key = ?', (key,)).fetchone()
    return row['value'] if row else default


def set_setting(key, value):
    with connect() as conn:
        conn.execute('INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value',
                     (key, None if value is None else str(value)))


def stats(days=30):
    agora = time.time()
    inicio_dia = agora - (agora % DIA)  # meia-noite UTC
    with connect() as conn:
        um = lambda sql, *p: conn.execute(sql, p).fetchone()[0]  # noqa: E731
        resumo = {
            'downloads_24h': um("SELECT COUNT(*) FROM downloads WHERE status='done' AND created_at >= ?", agora - DIA),
            'failed_24h': um("SELECT COUNT(*) FROM downloads WHERE status IN ('error','rejected') AND created_at >= ?",
                             agora - DIA),
            'downloads_7d': um("SELECT COUNT(*) FROM downloads WHERE status='done' AND created_at >= ?", agora - 7 * DIA),
            'users_total': um('SELECT COUNT(*) FROM users'),
            'users_new_7d': um('SELECT COUNT(*) FROM users WHERE created_at >= ?', agora - 7 * DIA),
            'premium_active': um("SELECT COUNT(*) FROM users WHERE plan='premium' AND "
                                 "(premium_until IS NULL OR premium_until > ?)", agora),
            'revenue_30d': um("SELECT COALESCE(SUM(amount),0) FROM payments WHERE status='approved' AND updated_at >= ?",
                              agora - 30 * DIA),
            'revenue_total': um("SELECT COALESCE(SUM(amount),0) FROM payments WHERE status='approved'"),
            'tickets_open': um("SELECT COUNT(*) FROM tickets WHERE status='open'"),
            'tickets_unread': um('SELECT COUNT(*) FROM tickets WHERE unread_admin = 1'),
            'unique_ips_24h': um('SELECT COUNT(DISTINCT ip) FROM downloads WHERE created_at >= ?', agora - DIA),
        }
        serie = _rows(conn.execute(
            """SELECT CAST((created_at - ?) / 86400 AS INTEGER) AS dia,
                      SUM(CASE WHEN status='done' AND plan='free' THEN 1 ELSE 0 END) AS free,
                      SUM(CASE WHEN status='done' AND plan='premium' THEN 1 ELSE 0 END) AS premium,
                      SUM(CASE WHEN status!='done' THEN 1 ELSE 0 END) AS failed
               FROM downloads WHERE created_at >= ? GROUP BY dia""",
            (inicio_dia - (days - 1) * DIA, inicio_dia - (days - 1) * DIA)))
        top = _rows(conn.execute(
            "SELECT title, url, COUNT(*) AS n FROM downloads WHERE status='done' AND created_at >= ? "
            "GROUP BY url ORDER BY n DESC LIMIT 10", (agora - 7 * DIA,)))
        formatos = _rows(conn.execute(
            "SELECT fmt, COUNT(*) AS n FROM downloads WHERE status='done' AND created_at >= ? GROUP BY fmt ORDER BY n DESC",
            (agora - 30 * DIA,)))
        erros = _rows(conn.execute(
            "SELECT error, COUNT(*) AS n FROM downloads WHERE status!='done' AND created_at >= ? "
            "GROUP BY error ORDER BY n DESC LIMIT 5", (agora - 7 * DIA,)))
    por_dia = {s['dia']: s for s in serie}
    resumo['series'] = [
        {'day': inicio_dia - (days - 1 - i) * DIA,
         'free': (por_dia.get(i) or {}).get('free', 0) or 0,
         'premium': (por_dia.get(i) or {}).get('premium', 0) or 0,
         'failed': (por_dia.get(i) or {}).get('failed', 0) or 0}
        for i in range(days)
    ]
    resumo['top'] = top
    resumo['formats'] = formatos
    resumo['top_errors'] = erros
    return resumo


# =====================================================================
# Retenção (cumpre os prazos da Política de privacidade)
# =====================================================================

def purge_retention():
    """Apaga dados vencidos: registros de download (6 meses), tokens de login, suporte encerrado (1 ano)."""
    agora = time.time()
    with connect() as conn:
        apagados = {
            'downloads': conn.execute('DELETE FROM downloads WHERE created_at < ?', (agora - 183 * DIA,)).rowcount,
            'login_tokens': conn.execute('DELETE FROM login_tokens WHERE expires_at < ?', (agora - DIA,)).rowcount,
            'free_usage': conn.execute('DELETE FROM free_usage WHERE created_at < ?', (agora - 3600,)).rowcount,
            'tickets': conn.execute("DELETE FROM tickets WHERE updated_at < ?", (agora - 365 * DIA,)).rowcount,
        }
    return apagados
