"""Processo isolado que baixa UM link e conversa com o servidor por JSON (uma linha por evento).

O servidor (ytd/jobs.py) inicia:  python -m ytd.worker   e envia o pedido em JSON pelo stdin.
Eventos enviados no stdout:
    {"event": "progress", "progress": 42.0, "stage": "Baixando: ..."}
    {"event": "entry", "title": ..., "url": ..., "duration": ..., "files": [...]}
    {"event": "rejected", "title": ..., "message": ...}
    {"event": "error", "message": ...}
    {"event": "done"}
"""
import json
import os
import sys


def main():
    pedido = json.loads(sys.stdin.read())
    # Tudo que o yt-dlp escreveria no stdout vai para o stderr (que o servidor manda para o log).
    stdout_real = sys.stdout
    sys.stdout = sys.stderr

    from ytd import core  # importado aqui para pegar sempre a versão instalada do yt-dlp

    def emit(**dados):
        stdout_real.write(json.dumps(dados, ensure_ascii=False) + '\n')
        stdout_real.flush()

    ultimo = {'p': -1}

    def on_progress(d):
        if d.get('status') == 'downloading':
            total = d.get('total_bytes') or d.get('total_bytes_estimate') or 0
            p = min(90.0, d.get('downloaded_bytes', 0) / total * 90) if total else 0.0
            if abs(p - ultimo['p']) >= 1 or p == 0:
                ultimo['p'] = p
                titulo = (d.get('info_dict') or {}).get('title')
                emit(event='progress', progress=round(p, 1), stage=f'Baixando: {titulo}' if titulo else 'Baixando')
        elif d.get('status') == 'finished':
            emit(event='progress', progress=90.0, stage='Convertendo')

    def on_pp(d):
        if d.get('postprocessor') == 'MetadataParser':  # roda antes do download; não é conversão
            return
        if d.get('status') == 'started':
            nomes = {'FFmpegExtractAudio': 'Convertendo áudio', 'FFmpegVideoConvertor': 'Convertendo vídeo',
                     'EmbedThumbnail': 'Colocando a capa', 'FFmpegMetadata': 'Gravando artista e título',
                     'Merger': 'Juntando áudio e vídeo'}
            emit(event='progress', progress=93.0, stage=nomes.get(d.get('postprocessor'), 'Convertendo'))

    rejeitados = []
    try:
        entradas = core.download(
            pedido['url'], pedido['output_dir'], rejected=rejeitados,
            kind=pedido['kind'], fmt=pedido['fmt'], quality=pedido.get('quality'),
            playlist=pedido.get('playlist', False), max_playlist=pedido.get('max_playlist'),
            max_duration_min=pedido.get('max_duration_min'),
            progress_hook=on_progress, postprocessor_hook=on_pp, quiet=True)
        for r in rejeitados:
            emit(event='rejected', **r)
        for e in entradas:
            # Vídeos recusados pelo filtro também voltam com metadados: só conta o que virou arquivo de verdade.
            arquivos = [d.get('filepath') for d in e.get('requested_downloads', [])
                        if d.get('filepath') and os.path.exists(d['filepath'])]
            if not arquivos:
                continue
            emit(event='entry', title=e.get('title') or e.get('id'), url=e.get('webpage_url') or pedido['url'],
                 duration=e.get('duration'),
                 size=sum(os.path.getsize(a) for a in arquivos if os.path.exists(a)), files=arquivos)
        emit(event='done')
    except core.DownloadError as e:
        for r in rejeitados:
            emit(event='rejected', **r)
        emit(event='error', message=str(e))
    except Exception as e:  # erro inesperado: vai para o log com detalhes
        import traceback
        traceback.print_exc()
        emit(event='error', message=f'Erro interno: {e}')


if __name__ == '__main__':
    main()
