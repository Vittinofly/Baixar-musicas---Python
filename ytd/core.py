"""Núcleo do downloader: valida links, monta as opções do yt-dlp e executa o download.

É usado pela versão de terminal (main.py), pelo processo de download do servidor
(ytd/worker.py) e pela prévia do site. As regras de formato/qualidade ficam só aqui.
"""
import json
import os
import re
import shutil
import threading
import time
import urllib.parse
import urllib.request
from urllib.parse import urlparse

from .config import BASE_DIR

AUDIO_FORMATS = ('mp3', 'm4a')
VIDEO_FORMATS = ('mp4', 'avi')
FORMAT_ALIASES = {'divx': 'avi'}
AVI_MAX_HEIGHT = 720  # conversão para Xvid é pesada; acima disso não compensa

YOUTUBE_HOSTS = {
    'youtube.com', 'www.youtube.com', 'm.youtube.com', 'music.youtube.com',
    'youtu.be', 'www.youtu.be', 'youtube-nocookie.com', 'www.youtube-nocookie.com',
}
TEMP_SUFFIXES = ('.part', '.ytdl', '.temp', '.tmp', '.webp', '.jpg', '.png')


class DownloadError(Exception):
    """Erro com mensagem amigável para mostrar ao usuário."""


# ---------------------------------------------------------------------
# Links e formatos
# ---------------------------------------------------------------------

def split_links(texto):
    partes = re.split(r'[,\s]+', texto or '')
    return [p.strip().strip('"').strip("'") for p in partes if p.strip().strip('"').strip("'")]


def is_youtube_url(url):
    try:
        partes = urlparse(url)
    except ValueError:
        return False
    return partes.scheme in ('http', 'https') and (partes.hostname or '').lower() in YOUTUBE_HOSTS


def is_playlist_url(url):
    q = urllib.parse.parse_qs(urlparse(url).query)
    return 'list' in q and 'v' not in q


def normalize_format(kind, fmt):
    fmt = FORMAT_ALIASES.get((fmt or '').lower(), (fmt or '').lower())
    if kind == 'audio':
        fmt = fmt or 'mp3'
        if fmt not in AUDIO_FORMATS:
            raise DownloadError(f'Formato de áudio inválido: {fmt}. Use: {", ".join(AUDIO_FORMATS)}.')
    elif kind == 'video':
        fmt = fmt or 'mp4'
        if fmt not in VIDEO_FORMATS:
            raise DownloadError(f'Formato de vídeo inválido: {fmt}. Use: {", ".join(VIDEO_FORMATS)}.')
    else:
        raise DownloadError('Tipo inválido. Use "audio" ou "video".')
    return fmt


def normalize_quality(kind, fmt, quality, limits):
    """Valida a qualidade pedida contra o plano. Áudio = kbps, vídeo = altura (p)."""
    if kind == 'audio':
        opcoes, padrao, rotulo = limits['audio_kbps'], limits['default_kbps'], 'kbps'
    else:
        opcoes = [h for h in limits['video_heights'] if fmt != 'avi' or h <= AVI_MAX_HEIGHT]
        padrao = min(limits['default_height'], max(opcoes))
        rotulo = 'p'
    if quality in (None, '', 'auto'):
        return padrao
    try:
        q = int(str(quality).rstrip('kbps').rstrip('p'))
    except ValueError:
        raise DownloadError('Qualidade inválida.')
    if q not in opcoes:
        maximo = max(opcoes)
        if q > maximo:
            raise DownloadError(f'Qualidade {q}{rotulo} não está disponível no seu plano (máximo {maximo}{rotulo}).')
        raise DownloadError(f'Qualidade inválida. Opções: {", ".join(f"{o}{rotulo}" for o in opcoes)}.')
    return q


# ---------------------------------------------------------------------
# Dependências
# ---------------------------------------------------------------------

def find_ffmpeg():
    local = os.path.join(BASE_DIR, 'ffmpeg.exe')
    if os.name == 'nt' and os.path.isfile(local):
        return local
    return shutil.which('ffmpeg')


def find_node():
    return shutil.which('node')


def check_dependencies():
    problemas = []
    if not find_ffmpeg():
        problemas.append('FFmpeg não encontrado (coloque ffmpeg.exe na pasta do projeto ou instale no PATH).')
    if not find_node():
        problemas.append('Node.js não encontrado. Instale o Node.js (nodejs.org): o YouTube exige um runtime JavaScript.')
    return problemas


def ytdlp_version():
    try:
        from importlib.metadata import version
        return version('yt-dlp')
    except Exception:
        return None


def _base_opts(quiet=True):
    opts = {
        'quiet': quiet,
        'no_warnings': quiet,
        'noprogress': quiet,
        'allowed_extractors': [r'youtube.*'],
        'remote_components': {'ejs:github'},
    }
    node = find_node()
    if node:
        opts['js_runtimes'] = {'node': {'path': node}}
    return opts


# ---------------------------------------------------------------------
# Opções de download
# ---------------------------------------------------------------------

def duration_filter(max_duration_min, rejeitados):
    """Pula vídeos acima do limite do plano e transmissões ao vivo, guardando o motivo."""
    def filtro(info, *, incomplete=False):
        if incomplete:
            return None
        titulo = info.get('title') or info.get('id')
        if info.get('is_live') or info.get('live_status') in ('is_live', 'is_upcoming'):
            motivo = f'"{titulo}" é uma transmissão ao vivo e não pode ser baixado.'
            rejeitados.append({'url': info.get('webpage_url'), 'title': titulo, 'message': motivo})
            return motivo
        duracao = info.get('duration')
        if max_duration_min and duracao and duracao > max_duration_min * 60:
            motivo = (f'"{titulo}" tem {format_duration(duracao)}. O limite do seu plano é '
                      f'{max_duration_min} min por vídeo.')
            rejeitados.append({'url': info.get('webpage_url'), 'title': titulo, 'message': motivo,
                               'duration': duracao})
            return motivo
        return None
    return filtro


def build_options(output_dir, kind='audio', fmt='mp3', quality=None, playlist=False, max_playlist=None,
                  max_duration_min=None, rejected=None, progress_hook=None, postprocessor_hook=None,
                  embed_thumbnail=True, quiet=False):
    fmt = normalize_format(kind, fmt)
    if quality in (None, '', 'auto'):
        quality = 320 if kind == 'audio' else 1080
    quality = int(quality)

    opts = _base_opts(quiet)
    opts.update({
        'outtmpl': os.path.join(output_dir, '%(title)s [%(id)s].%(ext)s'),
        'noplaylist': not playlist,
        'ignoreerrors': 'only_download' if playlist else False,
        'ffmpeg_location': find_ffmpeg(),
        'windowsfilenames': True,
        'overwrites': True,
        'retries': 5,
        'fragment_retries': 5,
        'concurrent_fragment_downloads': 4,
    })
    if playlist and max_playlist:
        opts['playlist_items'] = f'1:{int(max_playlist)}'
    if max_duration_min is not None or rejected is not None:
        opts['match_filter'] = duration_filter(max_duration_min, rejected if rejected is not None else [])
    if progress_hook:
        opts['progress_hooks'] = [progress_hook]
    if postprocessor_hook:
        opts['postprocessor_hooks'] = [postprocessor_hook]

    # Metadados: usa artista/faixa do YouTube Music quando existir, senão o canal e o título.
    from yt_dlp.postprocessor.metadataparser import MetadataParserPP
    metadados = {
        'key': 'MetadataParser', 'when': 'pre_process',
        'actions': [
            (MetadataParserPP.Actions.INTERPRET, '%(artist,artists.0,creator,uploader)s', '%(meta_artist)s'),
            (MetadataParserPP.Actions.INTERPRET, '%(track,title)s', '%(meta_title)s'),
            (MetadataParserPP.Actions.REPLACE, 'meta_artist', r'\s+-\s+Topic$', ''),
        ],
    }
    capa = []
    if embed_thumbnail and not (kind == 'video' and fmt == 'avi'):  # AVI não suporta capa
        opts['writethumbnail'] = True
        capa = [{'key': 'FFmpegThumbnailsConvertor', 'format': 'jpg', 'when': 'before_dl'},
                {'key': 'EmbedThumbnail', 'already_have_thumbnail': False}]

    if kind == 'audio':
        opts['format'] = 'bestaudio/best'
        opts['postprocessors'] = [
            metadados,
            {'key': 'FFmpegExtractAudio', 'preferredcodec': fmt, 'preferredquality': str(quality)},
            {'key': 'FFmpegMetadata', 'add_metadata': True},
            *capa,
        ]
    elif fmt == 'mp4':
        h = quality
        if h <= 1080:
            # Prioriza H.264 (avc1): AV1/VP9 dentro do MP4 não abre em muitos players e TVs.
            opts['format'] = (f'bestvideo[height<={h}][ext=mp4][vcodec^=avc1]+bestaudio[ext=m4a]/'
                              f'bestvideo[height<={h}][ext=mp4]+bestaudio[ext=m4a]/'
                              f'bestvideo[height<={h}]+bestaudio/best[height<={h}]/best')
        else:  # 1440p/4K só existem em VP9/AV1 no YouTube
            opts['format'] = f'bestvideo[height<={h}]+bestaudio/best[height<={h}]/best'
        opts['merge_output_format'] = 'mp4'
        opts['postprocessors'] = [
            metadados,
            {'key': 'FFmpegVideoConvertor', 'preferedformat': 'mp4'},
            {'key': 'FFmpegMetadata', 'add_metadata': True},
            *capa,
        ]
    else:  # avi (Xvid/DivX)
        h = min(quality, AVI_MAX_HEIGHT)
        opts['format'] = f'bestvideo[height<={h}]+bestaudio/best[height<={h}]/best'
        opts['merge_output_format'] = 'mkv'
        opts['postprocessors'] = [metadados, {'key': 'FFmpegVideoConvertor', 'preferedformat': 'avi'}]
    return opts


def collect_entries(info):
    if not info:
        return []
    if info.get('_type') == 'playlist' or 'entries' in info:
        itens = []
        for entrada in info.get('entries') or []:
            itens.extend(collect_entries(entrada))
        return itens
    return [info]


def list_output_files(output_dir):
    if not os.path.isdir(output_dir):
        return []
    return sorted(
        os.path.join(output_dir, nome) for nome in os.listdir(output_dir)
        if os.path.isfile(os.path.join(output_dir, nome)) and not nome.lower().endswith(TEMP_SUFFIXES)
    )


def clean_error(mensagem):
    mensagem = re.sub(r'\x1b\[[0-9;]*m', '', str(mensagem)).replace('ERROR: ', '').strip()
    if 'Sign in to confirm' in mensagem or 'not a bot' in mensagem:
        return 'O YouTube bloqueou temporariamente o servidor. Tente de novo em alguns minutos.'
    if 'Video unavailable' in mensagem or 'This video is unavailable' in mensagem:
        return 'Vídeo indisponível (removido, privado ou bloqueado na região).'
    if 'age' in mensagem.lower() and 'confirm' in mensagem.lower():
        return 'Vídeo com restrição de idade: não é possível baixar sem login no YouTube.'
    if 'Private video' in mensagem:
        return 'Vídeo privado.'
    return re.sub(r'^\[[^\]]+\] [\w-]+: ', '', mensagem)


def download(url, output_dir, rejected=None, **kwargs):
    """Baixa um link e devolve a lista de vídeos concluídos (dicts do yt-dlp)."""
    import yt_dlp

    if not is_youtube_url(url):
        raise DownloadError(f'Link não suportado: {url}. Use um link do YouTube.')
    problemas = check_dependencies()
    if problemas:
        raise DownloadError(' '.join(problemas))
    os.makedirs(output_dir, exist_ok=True)
    rejeitados = rejected if rejected is not None else []
    opts = build_options(output_dir, rejected=rejeitados, **kwargs)
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except yt_dlp.utils.DownloadError as e:
        raise DownloadError(clean_error(e)) from e
    return [e for e in collect_entries(info) if e and e.get('requested_downloads')]


# ---------------------------------------------------------------------
# Prévia (título, canal, capa e duração antes de baixar)
# ---------------------------------------------------------------------

_cache = {}
_cache_lock = threading.Lock()
CACHE_TTL = 15 * 60


def _cache_get(chave):
    with _cache_lock:
        item = _cache.get(chave)
        if item and item[0] > time.time():
            return item[1]
        _cache.pop(chave, None)
    return None


def _cache_set(chave, valor):
    with _cache_lock:
        if len(_cache) > 2000:
            _cache.clear()
        _cache[chave] = (time.time() + CACHE_TTL, valor)


def format_duration(segundos):
    if not segundos:
        return ''
    segundos = int(segundos)
    h, resto = divmod(segundos, 3600)
    m, s = divmod(resto, 60)
    return f'{h}h{m:02d}min' if h else f'{m}:{s:02d} min'


def preview_quick(url):
    """Prévia rápida (~1 s) pelo oEmbed do YouTube: título, canal e capa."""
    chave = ('quick', url)
    if (dados := _cache_get(chave)) is not None:
        return dados
    endpoint = 'https://www.youtube.com/oembed?format=json&url=' + urllib.parse.quote(url, safe='')
    req = urllib.request.Request(endpoint, headers={'User-Agent': 'Mozilla/5.0 (YT Downloader preview)'})
    try:
        with urllib.request.urlopen(req, timeout=6) as r:
            o = json.load(r)
        dados = {'title': o.get('title'), 'channel': o.get('author_name'), 'thumbnail': o.get('thumbnail_url'),
                 'playlist': is_playlist_url(url)}
    except Exception:
        dados = {}
    _cache_set(chave, dados)
    return dados


def preview_full(url):
    """Prévia completa (duração, quantidade de vídeos da playlist) via yt-dlp, sem baixar."""
    import yt_dlp

    chave = ('full', url)
    if (dados := _cache_get(chave)) is not None:
        return dados
    opts = _base_opts(True)
    opts.update({'skip_download': True, 'extract_flat': 'in_playlist', 'noplaylist': not is_playlist_url(url),
                 # Para a prévia não precisamos dos formatos: pular o player deixa ~3x mais rápido.
                 'extractor_args': {'youtube': {'player_skip': ['js', 'configs'], 'skip': ['dash', 'hls']}}})
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False, process=is_playlist_url(url))
    except yt_dlp.utils.DownloadError as e:
        raise DownloadError(clean_error(e)) from e
    thumbs = info.get('thumbnails') or []
    if info.get('_type') == 'playlist':
        entradas = info.get('entries') or []
        dados = {'title': info.get('title'), 'channel': info.get('uploader') or info.get('channel'),
                 'thumbnail': thumbs[-1]['url'] if thumbs else None, 'playlist': True,
                 'count': info.get('playlist_count') or len(entradas),
                 'duration': sum((e or {}).get('duration') or 0 for e in entradas) or None}
    else:
        dados = {'title': info.get('title'), 'channel': info.get('artist') or info.get('channel') or info.get('uploader'),
                 'thumbnail': info.get('thumbnail') or (thumbs[-1]['url'] if thumbs else None),
                 'playlist': False, 'duration': info.get('duration'),
                 'is_live': info.get('live_status') in ('is_live', 'is_upcoming')}
    dados['duration_text'] = format_duration(dados.get('duration'))
    _cache_set(chave, dados)
    return dados
