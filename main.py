# Downloader de Músicas e Vídeos do YouTube feito por Victor Hugo em Python usando a biblioteca yt_dlp

import yt_dlp # Biblioteca para baixar vídeos do YouTube
import os # Biblioteca para manipulação de arquivos e diretórios

def download_video(urls, apenas_audio = True, baixar_playlist = False, video_format='mp4', audio_format='mp3'): # Função para baixar um vídeo do YouTube
    
    pasta_destino = r'escolha/seu/caminho/aqui' # Caminho da pasta de destino

    if not os.path.exists(pasta_destino): # Verifica se a pasta de destino existe
        os.makedirs(pasta_destino) # Cria a pasta de destino se ela não existir


    # Arquivo que registra o que já foi baixado para evitar repetições
    arquivo_historico = os.path.join(pasta_destino, 'historico_downloads.txt')

    ydl_opts = {
        'outtmpl': os.path.join(pasta_destino, '%(title)s.%(ext)s'), # Define o template de nome do arquivo de saída, usando o título do vídeo e a extensão apropriada
        'noplaylist': not baixar_playlist, # Define se deve baixar a playlist ou não
        'format': 'bestaudio/best' if apenas_audio else 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best', # Define o formato do vídeo a ser baixado
        'download_archive': arquivo_historico, # Arquivo para registrar downloads e evitar repetições
        'ignoreerrors': True, # Continua baixando mesmo se houver erros em alguns vídeos
    }

    if apenas_audio:
        ydl_opts['format'] = 'bestaudio/best'
        if audio_format == 'divx':
            ydl_opts['postprocessors'] = [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'libmp3lame',
                'preferredquality': '320',
            }, {
                'key': 'FFmpegVideoConvertor',
                'preferedformat': 'avi',
            }]
        else:  # mp3
            ydl_opts['postprocessors'] = [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3', # Define o formato de áudio preferido
                'preferredquality': '320', # Define a qualidade de áudio preferida
            }]
    else:
        if video_format == 'divx':
            ydl_opts['format'] = 'best'
            ydl_opts['merge_output_format'] = 'avi'
            ydl_opts['postprocessors'] = [{
                'key': 'FFmpegVideoConvertor',
                'preferedformat': 'avi',
            }]
        else:  # mp4
            ydl_opts['format'] = 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best'
            ydl_opts['merge_output_format'] = 'mp4'
            ydl_opts['postprocessors'] = [{
                'key': 'FFmpegVideoConvertor',
                'preferedformat': 'mp4',
            }]
    
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        for url_item in urls: # Itera sobre cada URL na lista de links
            try:
                url_limpa = url_item.strip() # Remove espaços em branco extras da URL
                if not url_limpa: continue # Pula URLs vazias

                print(f'-----Baixando: {url_limpa}-----') # Imprime o link do vídeo ou da playlist que está sendo baixado
                ydl.download([url_limpa]) # Baixa o vídeo ou a playlist
                print('-----Download concluído!-----') # Imprime uma mensagem indicando que o download foi concluído
            except Exception as e:
                print(f'Erro ao baixar: {e}')

if __name__=='__main__':
    print("=============== DOWNLOADER DE Músicas e Vídeos (Feito por Victor Hugo) ===============\n")

    link = input ("Cole o(s) link(s) do YouTube (separe por vírgula se for mais de um): ") # Solicita ao usuário o link do vídeo ou da playlist
    lista_de_links = link.split(',') # Divide a entrada em uma lista de links usando a vírgula como separador
    
    so_musica = input('Deseja baixar apenas o áudio? (s/n): ').lower() == 's' # Pergunta ao usuário se deseja baixar apenas o áudio
    baixar_playlist = input('Deseja baixar uma playlist? (s/n): ').lower() == 's' # Pergunta ao usuário se deseja baixar uma playlist
    
    video_format = 'mp4'
    audio_format = 'mp3'
    
    if so_musica:
        audio_format = input('Escolha o formato de áudio (mp3/divx): ').lower()
        if audio_format not in ['mp3', 'divx']:
            audio_format = 'mp3'  # default
    else:
        video_format = input('Escolha o formato de vídeo (divx/mp4): ').lower()
        if video_format not in ['divx', 'mp4']:
            video_format = 'mp4'  # default
    
    download_video(lista_de_links, apenas_audio=so_musica, baixar_playlist=baixar_playlist, video_format=video_format, audio_format=audio_format) # Chama a função para baixar o vídeo ou a playlist com as opções escolhidas pelo usuário