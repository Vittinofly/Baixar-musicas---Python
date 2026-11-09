"""Logs da aplicação.

Tudo vai para data/logs/app.log (um arquivo por dia, ~6 meses guardados) e para o console.
Formato de cada linha (fácil de ler e de filtrar no painel admin):

    2026-11-07 10:15:02 | INFO    | ytd.jobs     | download concluído job=ab12 titulo="..." segundos=8.2

Erros também vão para data/logs/errors.log, para achar problemas rápido.
"""
import logging
import os
import re
from collections import deque
from logging.handlers import RotatingFileHandler, TimedRotatingFileHandler

from . import config

FORMATO = '%(asctime)s | %(levelname)-7s | %(name)-12s | %(message)s'
DATA = '%Y-%m-%d %H:%M:%S'
LINHA_RE = re.compile(r'^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) \| (\w+)\s*\| (\S+)\s*\| (.*)$')

_configurado = False


def setup_logging():
    global _configurado
    if _configurado:
        return
    _configurado = True
    os.makedirs(config.LOG_DIR, exist_ok=True)
    formato = logging.Formatter(FORMATO, DATA)

    raiz = logging.getLogger('ytd')
    raiz.setLevel(getattr(logging, config.LOG_LEVEL, logging.INFO))
    raiz.propagate = False

    # Um arquivo por dia, guardando ~6 meses (prazo do Marco Civil da Internet, art. 15).
    arquivo = TimedRotatingFileHandler(os.path.join(config.LOG_DIR, 'app.log'), when='midnight', encoding='utf-8',
                                       backupCount=config.LOG_RETENTION_DAYS)
    arquivo.setFormatter(formato)
    raiz.addHandler(arquivo)

    erros = RotatingFileHandler(os.path.join(config.LOG_DIR, 'errors.log'), encoding='utf-8',
                                maxBytes=config.LOG_MAX_MB * 1024 * 1024, backupCount=3)
    erros.setLevel(logging.WARNING)
    erros.setFormatter(formato)
    raiz.addHandler(erros)

    console = logging.StreamHandler()
    console.setFormatter(formato)
    raiz.addHandler(console)

    # Logs do servidor Waitress (erros de conexão, fila cheia) no mesmo arquivo.
    waitress = logging.getLogger('waitress')
    waitress.setLevel(logging.WARNING)
    waitress.addHandler(arquivo)


def get(nome):
    return logging.getLogger(f'ytd.{nome}')


def kv(**campos):
    """Formata campos como chave=valor (strings com espaço ficam entre aspas)."""
    partes = []
    for k, v in campos.items():
        if v is None or v == '':
            continue
        v = str(v).replace('\n', ' ')
        if ' ' in v or '"' in v:
            v = '"' + v.replace('"', "'") + '"'
        partes.append(f'{k}={v}')
    return ' '.join(partes)


def tail(linhas=500, nivel=None, busca=None, arquivo='app.log'):
    """Lê as últimas linhas do log (mais recentes primeiro), com filtro de nível e texto."""
    caminho = os.path.join(config.LOG_DIR, arquivo)
    if not os.path.exists(caminho):
        return []
    niveis = {'DEBUG': 10, 'INFO': 20, 'WARNING': 30, 'ERROR': 40, 'CRITICAL': 50}
    minimo = niveis.get((nivel or '').upper(), 0)
    busca = (busca or '').lower()
    ultimas = deque(maxlen=20000)
    with open(caminho, encoding='utf-8', errors='replace') as f:
        for linha in f:
            ultimas.append(linha.rstrip('\n'))
    resultado = []
    for linha in reversed(ultimas):
        m = LINHA_RE.match(linha)
        if m:
            data, lvl, origem, msg = m.groups()
        else:  # continuação de traceback
            data, lvl, origem, msg = '', 'ERROR', '', linha
        if niveis.get(lvl, 0) < minimo:
            continue
        if busca and busca not in linha.lower():
            continue
        resultado.append({'time': data, 'level': lvl, 'source': origem, 'message': msg})
        if len(resultado) >= linhas:
            break
    return resultado
