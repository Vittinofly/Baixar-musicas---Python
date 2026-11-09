# Downloader de Músicas e Vídeos do YouTube feito por Victor Hugo em Python usando a biblioteca yt_dlp
#
# Versão de TERMINAL (uso pessoal, salva direto numa pasta do computador).
# A versão Web (Free/Premium) fica em app.py. As duas usam o mesmo núcleo: ytd/core.py
#
# Como rodar:  python main.py
# Pasta de destino: ~/Downloads/YT-Downloader (mude com a variável de ambiente YTD_CLI_DIR)

import sys

from ytd import config, core


def ler_links():
    print('Cole os links do YouTube abaixo. Separe por vírgula ou por linha e finalize com uma linha em branco.')
    print("O caractere '&' dos links é aceito normalmente.")
    linhas = []
    while True:
        try:
            linha = input()
        except EOFError:
            break
        if not linha.strip():
            break
        linhas.append(linha)
    return core.split_links('\n'.join(linhas))


def pergunta_sim_nao(mensagem):
    """Faz uma pergunta e retorna True para 's' e False para 'n'."""
    while True:
        resposta = input(mensagem).strip().lower()
        if resposta in ('s', 'sim'):
            return True
        if resposta in ('n', 'nao', 'não'):
            return False
        print("  ⚠️  Digite 's' para sim ou 'n' para não.")


def pergunta_opcao(mensagem, opcoes):
    """Faz uma pergunta e retorna uma das opções válidas."""
    while True:
        resposta = input(mensagem).strip().lower()
        resposta = core.FORMAT_ALIASES.get(resposta, resposta)
        if resposta in opcoes:
            return resposta
        print(f"  ⚠️  Opção inválida. Escolha entre: {'/'.join(opcoes)}")


def baixar(urls, kind, fmt, playlist):
    pasta = config.CLI_DOWNLOAD_DIR
    print(f'Pasta de destino: {pasta}')
    for url in urls:
        print(f'----- Baixando: {url} -----')
        try:
            entradas = core.download(url, pasta, kind=kind, fmt=fmt, playlist=playlist)
        except core.DownloadError as e:
            print(f'Erro ao baixar {url}: {e}')
            continue
        if not entradas:
            print('Nenhum arquivo foi baixado. Verifique as mensagens do yt-dlp acima.')
            continue
        print('----- Download concluído! -----')
        for entrada in entradas:
            for arquivo in entrada.get('requested_downloads', []):
                print(f"Arquivo salvo: {arquivo.get('filepath')}")


def main():
    print('=============== DOWNLOADER DE Músicas e Vídeos (Feito por Victor Hugo) ===============\n')

    problemas = core.check_dependencies()
    if problemas:
        for p in problemas:
            print(f'⚠️  {p}')
        sys.exit(1)

    links = ler_links()
    if not links:
        print('Nenhum link informado. Saindo.')
        sys.exit(1)
    invalidos = [l for l in links if not core.is_youtube_url(l)]
    if invalidos:
        print(f'Estes links não são do YouTube e serão ignorados: {", ".join(invalidos)}')
        links = [l for l in links if l not in invalidos]
        if not links:
            sys.exit(1)

    apenas_audio = pergunta_sim_nao('\nDeseja baixar apenas o áudio? (s/n): ')
    playlist = pergunta_sim_nao('Deseja baixar uma playlist inteira? (s/n): ')
    if apenas_audio:
        fmt = pergunta_opcao(f'Escolha o formato de áudio ({"/".join(core.AUDIO_FORMATS)}): ', core.AUDIO_FORMATS)
    else:
        fmt = pergunta_opcao('Escolha o formato de vídeo (mp4/avi — avi = DivX/Xvid): ', core.VIDEO_FORMATS)

    print()
    baixar(links, 'audio' if apenas_audio else 'video', fmt, playlist)


if __name__ == '__main__':
    main()
