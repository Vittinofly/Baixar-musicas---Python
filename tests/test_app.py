"""Testes da aplicação (sem internet: o processo de download é simulado).

Rodar:  python -m unittest discover -s tests -v
"""
import os
import re
import sys
import tempfile
import time
import unittest
import zipfile
import io
import json
from unittest import mock

TMP = tempfile.mkdtemp(prefix='ytd-test-')
os.environ.update({'YTD_DATA_DIR': TMP, 'YTD_FREE_LIMIT_PER_HOUR': '3', 'YTD_PREMIUM_DAILY_LIMIT': '5',
                   'YTD_ADMIN_EMAILS': 'chefe@teste.com', 'YTD_AUTO_UPDATE': '0', 'YTD_ENV': 'development'})
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ytd import config, core, db, jobs  # noqa: E402
from ytd.jobs import JobManager  # noqa: E402
import app as app_module  # noqa: E402

URL = 'https://www.youtube.com/watch?v=jNQXAC9IVRw'


def falso_run_link(job, url, max_playlist):
    """Simula o worker: cria o arquivo e emite os mesmos eventos que o processo real."""
    vid = url.rsplit('=', 1)[-1]
    if vid == 'longo':
        yield {'event': 'rejected', 'url': url, 'title': 'Show inteiro',
               'message': f'"Show inteiro" tem 1h00min. O limite do seu plano é {job.max_duration_min} min por vídeo.'}
        yield {'event': 'done'}
        return
    if vid == 'quebrado':
        yield {'event': 'error', 'message': 'Unable to extract player response'}
        return
    os.makedirs(job.dir, exist_ok=True)
    caminho = os.path.join(job.dir, f'Musica {vid}.{job.fmt}')
    with open(caminho, 'wb') as f:
        f.write(b'conteudo')
    yield {'event': 'progress', 'progress': 50.0, 'stage': 'Baixando'}
    yield {'event': 'entry', 'title': f'Musica {vid}', 'url': url, 'duration': 120, 'size': 8, 'files': [caminho]}
    yield {'event': 'done'}


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.patch = mock.patch.object(jobs, 'run_link', side_effect=falso_run_link)
        cls.patch.start()
        cls.quebras = []
        cls.app = app_module.create_app(JobManager(max_workers=1, on_breakage=cls.quebras.append), background=False)
        cls.app.config['TESTING'] = True

    @classmethod
    def tearDownClass(cls):
        cls.patch.stop()

    def setUp(self):
        self.client = self.app.test_client()
        with db.connect() as conn:
            conn.execute('DELETE FROM free_usage')
            conn.execute('DELETE FROM downloads')

    def csrf(self, client=None):
        client = client or self.client
        client.get('/')
        with client.session_transaction() as s:
            return s['csrf']

    def esperar(self, job_id, client=None):
        client = client or self.client
        for _ in range(200):
            dados = client.get(f'/api/jobs/{job_id}').get_json()
            if dados['status'] in ('done', 'error'):
                return dados
            time.sleep(0.02)
        self.fail('job não terminou')

    def login(self, email, client=None):
        client = client or self.client
        token = self.csrf(client)
        r = client.post('/entrar', data={'email': email, 'csrf': token, 'proximo': '/conta'})
        self.assertEqual(r.status_code, 200)
        link = re.search(r'href="(http[^"]+/entrar/[^"]+)"', r.get_data(as_text=True)).group(1)
        r = client.get(link[link.index('/entrar/'):])
        self.assertEqual(r.status_code, 302)
        return client

    def premium_key(self, email):
        return {'Authorization': f'Bearer {db.create_user(email, "premium", 30)}'}


class NucleoTest(unittest.TestCase):
    def test_links(self):
        self.assertTrue(core.is_youtube_url(URL))
        self.assertTrue(core.is_youtube_url('https://youtu.be/abc?si=x&t=1'))
        self.assertFalse(core.is_youtube_url('http://169.254.169.254/latest'))
        self.assertFalse(core.is_youtube_url('https://youtube.com.evil.com/watch?v=1'))
        self.assertTrue(core.is_playlist_url('https://www.youtube.com/playlist?list=PL1'))
        self.assertFalse(core.is_playlist_url('https://www.youtube.com/watch?v=a&list=PL1'))

    def test_qualidade_por_plano(self):
        free, premium = config.limits_for('free'), config.limits_for('premium')
        self.assertEqual(core.normalize_quality('audio', 'mp3', None, free), 192)
        self.assertEqual(core.normalize_quality('audio', 'mp3', None, premium), 320)
        with self.assertRaisesRegex(core.DownloadError, 'plano'):
            core.normalize_quality('audio', 'mp3', 320, free)
        with self.assertRaisesRegex(core.DownloadError, 'plano'):
            core.normalize_quality('video', 'mp4', 2160, free)
        self.assertEqual(core.normalize_quality('video', 'avi', None, premium), 720)  # AVI limitado a 720p

    def test_opcoes_ytdlp(self):
        opts = core.build_options(TMP, kind='audio', fmt='mp3', quality=192, max_duration_min=15, rejected=[])
        chaves = [p['key'] for p in opts['postprocessors']]
        self.assertIn('EmbedThumbnail', chaves)  # capa no MP3
        self.assertIn('FFmpegMetadata', chaves)  # artista e título
        self.assertEqual(opts['postprocessors'][1]['preferredquality'], '192')
        opts = core.build_options(TMP, kind='video', fmt='mp4', quality=720)
        self.assertIn('height<=720', opts['format'])
        self.assertIn('avc1', opts['format'])

    def test_filtro_de_duracao(self):
        rejeitados = []
        filtro = core.duration_filter(15, rejeitados)
        self.assertIsNone(filtro({'title': 'curta', 'duration': 600}))
        self.assertIsNotNone(filtro({'title': 'longa', 'duration': 16 * 60}))
        self.assertIsNotNone(filtro({'title': 'live', 'is_live': True}))
        self.assertEqual(len(rejeitados), 2)


class FreeTest(Base):
    def test_paginas_publicas(self):
        for rota in ('/', '/termos', '/privacidade', '/entrar'):
            r = self.client.get(rota)
            self.assertEqual(r.status_code, 200, rota)
        self.assertIn('LGPD', self.client.get('/privacidade').get_data(as_text=True))

    def test_baixa_um_link_e_entrega(self):
        r = self.client.post('/api/jobs', json={'url': URL, 'kind': 'audio', 'fmt': 'mp3'})
        self.assertEqual(r.status_code, 202)
        job = self.esperar(r.get_json()['id'])
        self.assertEqual(job['status'], 'done')
        self.assertEqual(job['quality'], 192)
        arquivo = self.client.get(job['download_url'])
        self.assertEqual(arquivo.data, b'conteudo')
        arquivo.close()
        with db.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM downloads WHERE status='done'").fetchone()[0], 1)

    def test_bloqueia_recursos_premium(self):
        self.assertEqual(self.client.post('/api/jobs', json={'urls': [URL, 'https://youtu.be/b']}).status_code, 403)
        self.assertEqual(self.client.post('/api/jobs', json={'url': URL, 'playlist': True}).status_code, 403)
        self.assertEqual(self.client.post('/api/jobs', json={'url': 'https://www.youtube.com/playlist?list=PL1'}).status_code, 403)
        r = self.client.post('/api/jobs', json={'url': URL, 'kind': 'audio', 'fmt': 'mp3', 'quality': 320})
        self.assertEqual(r.status_code, 403)
        self.assertTrue(r.get_json()['upgrade'])
        self.assertEqual(self.client.get('/api/history').status_code, 401)

    def test_limite_por_hora_e_devolucao_em_falha(self):
        # Falha (vídeo longo recusado) devolve o crédito
        job = self.esperar(self.client.post('/api/jobs', json={'url': 'https://www.youtube.com/watch?v=longo'}).get_json()['id'])
        self.assertEqual(job['status'], 'error')
        self.assertIn('15 min', job['errors'][0]['message'])
        self.assertEqual(db.free_usage_count('127.0.0.1'), 0)
        for _ in range(3):
            self.assertEqual(self.client.post('/api/jobs', json={'url': URL}).status_code, 202)
        self.assertEqual(self.client.post('/api/jobs', json={'url': URL}).status_code, 429)

    def test_falha_tipica_pede_atualizacao_do_ytdlp(self):
        job = self.esperar(self.client.post('/api/jobs', json={'url': 'https://www.youtube.com/watch?v=quebrado'}).get_json()['id'])
        self.assertEqual(job['status'], 'error')
        self.assertTrue(self.quebras)

    def test_rejeita_link_que_nao_e_youtube(self):
        self.assertEqual(self.client.post('/api/jobs', json={'url': 'http://localhost:22/'}).status_code, 400)


class ContaTest(Base):
    def test_login_pagamento_simulado_e_chave(self):
        c = self.login('cliente@teste.com')
        self.assertIn('Plano Free', c.get('/conta').get_data(as_text=True))
        token = self.csrf(c)
        r = c.post('/assinar', data={'csrf': token})
        self.assertEqual(r.status_code, 302)
        pid = int(r.headers['Location'].rstrip('/').rsplit('/', 1)[-1])
        r = c.post(f'/pagamento/simulado/{pid}', data={'csrf': token})
        self.assertEqual(r.status_code, 302)
        usuario = db.get_user_by_email('cliente@teste.com')
        self.assertTrue(db.is_premium(usuario))
        self.assertGreater(usuario['premium_until'], time.time() + 29 * 86400)
        # Gera a chave e usa na API como a extensão faria
        c.post('/conta/chave', data={'csrf': token})
        pagina = c.get('/conta').get_data(as_text=True)
        chave = re.search(r'(ytd_[\w-]+)', pagina).group(1)
        me = self.client.get('/api/me', headers={'Authorization': f'Bearer {chave}'}).get_json()
        self.assertEqual(me['plan'], 'premium')
        # Chave só aparece uma vez
        self.assertNotIn(chave, c.get('/conta').get_data(as_text=True))

    def test_link_de_login_e_de_uso_unico(self):
        c = self.client
        token = self.csrf(c)
        r = c.post('/entrar', data={'email': 'unico@teste.com', 'csrf': token})
        caminho = re.search(r'href="http[^"]+(/entrar/[^"]+)"', r.get_data(as_text=True)).group(1)
        self.assertEqual(c.get(caminho).status_code, 302)
        r = self.app.test_client().get(caminho)
        self.assertIn('/entrar', r.headers['Location'])  # segunda vez: expirado

    def test_redirecionamento_seguro(self):
        c = self.client
        token = self.csrf(c)
        r = c.post('/entrar', data={'email': 'redir@teste.com', 'csrf': token, 'proximo': '//site-malicioso.com'})
        caminho = re.search(r'href="http[^"]+(/entrar/[^"]+)"', r.get_data(as_text=True)).group(1)
        self.assertEqual(c.get(caminho).headers['Location'], '/conta')

    def test_csrf_obrigatorio(self):
        c = self.login('csrf@teste.com')
        self.assertEqual(c.post('/assinar', data={}).status_code, 403)

    def test_premium_vencido_perde_acesso(self):
        h = self.premium_key('vencido@teste.com')
        with db.connect() as conn:
            conn.execute("UPDATE users SET premium_until = ? WHERE email = 'vencido@teste.com'", (time.time() - 10,))
        self.assertEqual(self.client.get('/api/history', headers=h).status_code, 403)


class PremiumTest(Base):
    def test_varios_links_historico_baixar_de_novo_e_cota(self):
        h = self.premium_key('premium@teste.com')
        links = [URL, 'https://www.youtube.com/watch?v=segundo']
        r = self.client.post('/api/jobs', json={'urls': links, 'kind': 'audio', 'fmt': 'mp3', 'quality': 320}, headers=h)
        self.assertEqual(r.status_code, 202)
        job = self.esperar(r.get_json()['id'])
        self.assertEqual(job['status'], 'done')
        self.assertTrue(job['file_name'].endswith('.zip'))

        itens = self.client.get('/api/history?q=segundo', headers=h).get_json()['items']
        self.assertEqual(len(itens), 1)
        r = self.client.post(f'/api/history/{itens[0]["id"]}/redownload', json={'kind': 'video', 'fmt': 'mp4', 'quality': 2160}, headers=h)
        self.assertEqual(r.status_code, 202)
        self.assertTrue(self.esperar(r.get_json()['id'])['file_name'].endswith('.mp4'))
        self.assertEqual(self.client.delete(f'/api/history/{itens[0]["id"]}', headers=h).status_code, 204)

        # Cota diária (5 nos testes): 3 usados, pedir 3 de uma vez passa do limite
        r = self.client.post('/api/jobs', json={'urls': [URL, 'https://youtu.be/x', 'https://youtu.be/y']}, headers=h)
        self.assertEqual(r.status_code, 429)

    def test_video_de_20_min_passa_no_premium(self):
        self.assertEqual(config.limits_for('premium')['max_duration_min'], 30)
        self.assertEqual(config.limits_for('free')['max_duration_min'], 15)


class SuporteAdminTest(Base):
    def test_conversa_de_suporte_completa(self):
        r = self.client.post('/api/suporte', json={'name': 'Ana', 'email': 'ana@teste.com', 'message': 'Não baixa <script>'})
        self.assertEqual(r.status_code, 201)
        token = r.get_json()['token']
        self.client.post(f'/api/suporte/{token}/mensagens', json={'message': 'Alguém aí?'})

        admin = self.login('chefe@teste.com', self.app.test_client())
        csrf = self.csrf(admin)
        tickets = admin.get('/api/admin/suporte?status=open').get_json()['items']
        tid = next(t['id'] for t in tickets if t['email'] == 'ana@teste.com')
        detalhe = admin.get(f'/api/admin/suporte/{tid}').get_json()
        self.assertEqual(len(detalhe['messages']), 2)
        r = admin.post(f'/api/admin/suporte/{tid}/responder', json={'message': 'Oi Ana!'}, headers={'X-CSRF-Token': csrf})
        self.assertEqual(r.status_code, 200)
        visto = self.client.get(f'/api/suporte/{token}').get_json()
        self.assertTrue(visto['unread'])
        self.assertEqual(visto['messages'][-1]['body'], 'Oi Ana!')

    def test_admin_protegido_e_funcionando(self):
        self.assertEqual(self.client.get('/api/admin/resumo').status_code, 401)
        comum = self.login('comum@teste.com', self.app.test_client())
        self.assertEqual(comum.get('/api/admin/resumo').status_code, 403)
        self.assertEqual(comum.get('/admin').status_code, 403)

        admin = self.login('chefe@teste.com', self.app.test_client())
        self.assertEqual(admin.get('/admin').status_code, 200)
        for rota in ('/api/admin/resumo', '/api/admin/usuarios', '/api/admin/downloads', '/api/admin/pagamentos',
                     '/api/admin/logs?nivel=INFO', '/api/admin/sistema', '/api/admin/suporte'):
            self.assertEqual(admin.get(rota).status_code, 200, rota)
        resumo = admin.get('/api/admin/resumo').get_json()
        self.assertEqual(len(resumo['series']), 30)
        # sem CSRF, ações são recusadas
        alvo = db.get_or_create_user('alvo@teste.com')
        self.assertEqual(admin.post(f'/api/admin/usuarios/{alvo["id"]}/premium', json={'dias': 30}).status_code, 403)
        r = admin.post(f'/api/admin/usuarios/{alvo["id"]}/premium', json={'dias': 30},
                       headers={'X-CSRF-Token': self.csrf(admin)})
        self.assertTrue(r.get_json()['user']['plan'] == 'premium')

    def test_logs_registram_requisicoes(self):
        self.client.get('/termos')
        linhas = __import__('ytd.logs', fromlist=['tail']).tail(50, busca='rota=/termos')
        self.assertTrue(linhas)


class ExtensaoTest(Base):
    def test_zip_por_navegador(self):
        for nav, esperado in (('chrome', 'service_worker'), ('firefox', 'scripts')):
            r = self.client.get(f'/extensao/{nav}.zip')
            self.assertEqual(r.status_code, 200)
            z = zipfile.ZipFile(io.BytesIO(r.data))
            manifesto = json.loads(z.read('manifest.json'))
            self.assertIn(esperado, manifesto['background'])
            self.assertIn('http://localhost/*', manifesto['host_permissions'])
            r.close()

    def test_retencao(self):
        db.log_download(job_id='x', plan='free', url=URL, status='done', created_at=time.time() - 200 * 86400)
        self.assertGreaterEqual(db.purge_retention()['downloads'], 1)


if __name__ == '__main__':
    unittest.main()
