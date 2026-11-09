"""Fila de downloads em segundo plano.

Cada pedido vira um "job" com id aleatório. Cada link do job é baixado num processo
separado (ytd/worker.py), o que permite:
  - usar a versão do yt-dlp recém-atualizada sem reiniciar o servidor;
  - cancelar downloads travados (tempo máximo por link);
  - um erro grave num download não derrubar o servidor.

Esta fila vive na memória de um único processo. Para escalar para várias máquinas,
substitua JobManager por uma fila externa (Redis + RQ/Celery) mantendo submit()/get().
"""
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor

from . import config, core, db, logs

log = logs.get('jobs')

# Mensagens do yt-dlp que costumam indicar que o YouTube mudou e é preciso atualizar.
SINAIS_DE_QUEBRA = ('Unable to extract', 'nsig', 'Requested format is not available', 'HTTP Error 403',
                    'Signature extraction failed', 'Some formats may be missing', 'Unsupported URL',
                    'n challenge', 'player response')


class Job:
    def __init__(self, urls, kind, fmt, playlist, quality=None, user=None, max_playlist=None,
                 max_duration_min=None, ip=None, channel='site'):
        self.id = uuid.uuid4().hex
        self.urls = urls
        self.kind = kind
        self.fmt = fmt
        self.quality = quality
        self.playlist = playlist
        self.user = user
        self.plan = 'premium' if user else 'free'
        self.max_playlist = max_playlist
        self.max_duration_min = max_duration_min
        self.ip = ip
        self.channel = channel
        self.status = 'queued'
        self.progress = 0.0
        self.stage = 'Na fila'
        self.current = 0
        self.items_done = 0
        self.titles = []
        self.errors = []
        self.file_path = None
        self.file_name = None
        self.created_at = time.time()
        self.started_at = None
        self.finished_at = None
        self.dir = os.path.join(config.JOBS_DIR, self.id)

    def to_dict(self):
        return {
            'id': self.id, 'status': self.status, 'progress': round(self.progress, 1), 'stage': self.stage,
            'current': self.current, 'total_links': len(self.urls), 'items_done': self.items_done,
            'titles': self.titles, 'errors': self.errors, 'file_name': self.file_name,
            'download_url': f'/api/jobs/{self.id}/file' if self.status == 'done' else None,
            'kind': self.kind, 'fmt': self.fmt, 'quality': self.quality,
        }


def run_link(job, url, max_playlist):
    """Executa o worker para um link e devolve os eventos (gerador). Substituível nos testes."""
    pedido = {'url': url, 'output_dir': job.dir, 'kind': job.kind, 'fmt': job.fmt, 'quality': job.quality,
              'playlist': job.playlist, 'max_playlist': max_playlist, 'max_duration_min': job.max_duration_min}
    env = dict(os.environ, PYTHONIOENCODING='utf-8', PYTHONUTF8='1')
    proc = subprocess.Popen([sys.executable, '-m', 'ytd.worker'], cwd=config.BASE_DIR, env=env,
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, encoding='utf-8', errors='replace')
    proc.stdin.write(json.dumps(pedido))
    proc.stdin.close()

    estourou = threading.Event()

    def matar():
        estourou.set()
        log.warning('download excedeu o tempo e foi cancelado %s',
                    logs.kv(job=job.id[:8], url=url, minutos=config.JOB_TIMEOUT_MIN))
        proc.kill()

    timer = threading.Timer(config.JOB_TIMEOUT_MIN * 60, matar)
    timer.daemon = True
    timer.start()

    stderr_linhas = []

    def ler_stderr():
        for linha in proc.stderr:
            linha = linha.rstrip()
            if linha:
                stderr_linhas.append(linha)
                nivel = log.warning if ('ERROR' in linha or 'Traceback' in linha or 'Error' in linha) else log.debug
                nivel('yt-dlp %s', logs.kv(job=job.id[:8], saida=linha[:500]))

    leitor = threading.Thread(target=ler_stderr, daemon=True)
    leitor.start()
    try:
        for linha in proc.stdout:
            try:
                yield json.loads(linha)
            except json.JSONDecodeError:
                log.debug('saída inesperada do worker %s', logs.kv(job=job.id[:8], linha=linha.strip()[:300]))
        proc.wait()
    finally:
        timer.cancel()
        leitor.join(timeout=2)
    if estourou.is_set():
        yield {'event': 'error', 'message': f'O download passou de {config.JOB_TIMEOUT_MIN} min e foi cancelado.'}
    elif proc.returncode not in (0, None):
        yield {'event': 'error', 'message': 'O processo de download terminou com erro.',
               'detail': '\n'.join(stderr_linhas[-5:])}


class JobManager:
    def __init__(self, max_workers=None, on_breakage=None):
        self._jobs = {}
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=max_workers or config.MAX_WORKERS, thread_name_prefix='ytd-job')
        self.on_breakage = on_breakage  # chamado quando o yt-dlp parece desatualizado
        os.makedirs(config.JOBS_DIR, exist_ok=True)
        threading.Thread(target=self._cleanup_loop, daemon=True, name='ytd-cleanup').start()

    def submit(self, job):
        with self._lock:
            self._jobs[job.id] = job
        log.info('download criado %s', logs.kv(job=job.id[:8], plano=job.plan, canal=job.channel,
                                                usuario=job.user['email'] if job.user else None, ip=job.ip,
                                                links=len(job.urls), tipo=job.kind, formato=job.fmt,
                                                qualidade=job.quality, playlist=job.playlist))
        self._pool.submit(self._run, job)
        return job

    def get(self, job_id):
        with self._lock:
            return self._jobs.get(job_id)

    def queue_position(self, job):
        with self._lock:
            na_fila = sorted((j for j in self._jobs.values() if j.status == 'queued'), key=lambda j: j.created_at)
        return na_fila.index(job) + 1 if job in na_fila else 0

    def counts(self):
        with self._lock:
            jobs = list(self._jobs.values())
        return {'queued': sum(j.status == 'queued' for j in jobs), 'running': sum(j.status == 'running' for j in jobs),
                'workers': config.MAX_WORKERS}

    # ---------- execução ----------

    def _registrar(self, job, url, status, **extra):
        try:
            db.log_download(job_id=job.id, user_id=job.user['id'] if job.user else None, ip=job.ip, plan=job.plan,
                            channel=job.channel, url=url, kind=job.kind, fmt=job.fmt,
                            quality=str(job.quality or ''), status=status, **extra)
        except Exception:
            log.exception('falha ao gravar download no banco')

    def _run(self, job):
        job.status = 'running'
        job.stage = 'Preparando'
        job.started_at = time.time()
        log.info('download iniciado %s', logs.kv(job=job.id[:8], espera_s=round(job.started_at - job.created_at, 1)))
        try:
            for indice, url in enumerate(job.urls, start=1):
                job.current = indice
                job.progress = 0.0
                job.stage = 'Preparando'
                max_playlist = job.max_playlist
                if job.user:  # cota diária do Premium
                    restante = config.PREMIUM_DAILY_LIMIT - db.premium_downloads_last_24h(job.user['id'])
                    if restante <= 0:
                        msg = f'Você atingiu o limite de {config.PREMIUM_DAILY_LIMIT} downloads em 24 horas.'
                        job.errors.append({'url': url, 'message': msg})
                        self._registrar(job, url, 'rejected', error=msg)
                        continue
                    if job.playlist:
                        max_playlist = min(max_playlist or restante, restante)

                inicio = time.time()
                baixou = False
                for ev in run_link(job, url, max_playlist):
                    tipo = ev.get('event')
                    if tipo == 'progress':
                        job.progress = ev.get('progress', job.progress)
                        job.stage = ev.get('stage', job.stage)
                    elif tipo == 'entry':
                        baixou = True
                        job.items_done += 1
                        job.titles.append(ev.get('title'))
                        job.progress = 100.0
                        self._registrar(job, ev.get('url') or url, 'done', title=ev.get('title'),
                                        media_duration=ev.get('duration'), size_bytes=ev.get('size'),
                                        elapsed=round(time.time() - inicio, 1))
                        if job.user:
                            db.add_history(job.user['id'], ev.get('url') or url, ev.get('title'), job.kind, job.fmt)
                        log.info('item concluído %s', logs.kv(job=job.id[:8], titulo=ev.get('title'),
                                                               mb=round((ev.get('size') or 0) / 1e6, 1),
                                                               segundos=round(time.time() - inicio, 1)))
                    elif tipo == 'rejected':
                        job.errors.append({'url': ev.get('url') or url, 'message': ev.get('message')})
                        self._registrar(job, ev.get('url') or url, 'rejected', title=ev.get('title'),
                                        error=ev.get('message'), media_duration=ev.get('duration'))
                        log.info('item recusado %s', logs.kv(job=job.id[:8], motivo=ev.get('message')))
                    elif tipo == 'error':
                        msg = ev.get('message') or 'Erro desconhecido'
                        job.errors.append({'url': url, 'message': msg})
                        self._registrar(job, url, 'error', error=msg, elapsed=round(time.time() - inicio, 1))
                        log.warning('falha no download %s', logs.kv(job=job.id[:8], url=url, erro=msg,
                                                                     detalhe=ev.get('detail')))
                        if self.on_breakage and any(s in msg + (ev.get('detail') or '') for s in SINAIS_DE_QUEBRA):
                            self.on_breakage(msg)
                if not baixou and not any(e['url'] == url for e in job.errors):
                    job.errors.append({'url': url, 'message': 'Nenhum arquivo foi baixado deste link.'})

            arquivos = core.list_output_files(job.dir)
            if not arquivos:
                raise RuntimeError(job.errors[0]['message'] if job.errors else 'Nenhum arquivo foi baixado.')
            if len(arquivos) == 1:
                job.file_path = arquivos[0]
            else:
                job.stage = 'Compactando'
                job.file_path = os.path.join(job.dir, f'yt-downloader-{job.id[:8]}.zip')
                with zipfile.ZipFile(job.file_path, 'w', zipfile.ZIP_STORED) as zf:
                    for caminho in arquivos:
                        zf.write(caminho, os.path.basename(caminho))
            job.file_name = os.path.basename(job.file_path)
            job.progress = 100.0
            job.stage = 'Pronto'
            job.status = 'done'
        except Exception as e:
            if not job.errors:
                job.errors.append({'url': '', 'message': str(e)})
            if not job.user and job.ip:  # Free: não gasta o crédito quando nada foi entregue
                try:
                    db.refund_free_slot(job.ip)
                except Exception:
                    log.exception('falha ao devolver crédito Free')
            # Status por último: quem consulta o job já vê o crédito devolvido.
            job.stage = 'Erro'
            job.status = 'error'
        finally:
            job.finished_at = time.time()
            log.info('download finalizado %s', logs.kv(job=job.id[:8], status=job.status, itens=job.items_done,
                                                        erros=len(job.errors), arquivo=job.file_name,
                                                        segundos=round(job.finished_at - (job.started_at or job.created_at), 1)))

    # ---------- limpeza ----------

    def _cleanup_loop(self):
        ultima_retencao = 0.0
        while True:
            time.sleep(60)
            try:
                self.cleanup()
                if time.time() - ultima_retencao > 86400:  # uma vez por dia
                    ultima_retencao = time.time()
                    apagados = db.purge_retention()
                    log.info('limpeza de retenção %s', logs.kv(**apagados))
            except Exception:
                log.exception('falha na limpeza de arquivos')

    def cleanup(self):
        limite = time.time() - config.FILE_TTL_MIN * 60
        with self._lock:
            expirados = [j for j in self._jobs.values() if j.finished_at and j.finished_at < limite]
            for j in expirados:
                del self._jobs[j.id]
            ativos = set(self._jobs)
        for j in expirados:
            shutil.rmtree(j.dir, ignore_errors=True)
        if expirados:
            log.debug('arquivos expirados apagados %s', logs.kv(quantidade=len(expirados)))
        if os.path.isdir(config.JOBS_DIR):
            for nome in os.listdir(config.JOBS_DIR):
                caminho = os.path.join(config.JOBS_DIR, nome)
                if nome not in ativos and os.path.getmtime(caminho) < limite:
                    shutil.rmtree(caminho, ignore_errors=True)

