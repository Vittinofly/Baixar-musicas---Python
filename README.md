# Baixar musicas
Projetinho em Python para baixar musicas diretamente do Youtube

## Passo a Passo

1. **Instale as dependências necessárias:**
   - Certifique-se de ter o Python instalado (versão 3.6 ou superior).
   - Instale a biblioteca `yt-dlp` usando pip:
     ```
     pip install yt-dlp
     ```
   - Instale o FFmpeg (necessário para conversão de áudio/vídeo):
     - Baixe do site oficial: https://ffmpeg.org/download.html
     - Adicione ao PATH do sistema.

2. **Clone ou baixe o projeto:**
   - Baixe os arquivos `main.py` e outros necessários para uma pasta local.

3. **Execute o script:**
   - Abra o terminal na pasta do projeto.
   - Execute o comando:
     ```
     python main.py
     ```

4. **Siga as instruções no terminal:**
   - Cole o(s) link(s) do YouTube (separe por vírgula se múltiplos).
   - Escolha se deseja baixar apenas áudio (s/n).
   - Escolha se deseja baixar uma playlist (s/n).
   - Se áudio, escolha o formato (mp3/divx).
   - Se vídeo, escolha o formato (divx/mp4).

5. **Aguarde o download:**
   - Os arquivos serão salvos na pasta especificada no código (por padrão: `escolha/seu/caminho/preferido/`).
   - Verifique o histórico de downloads para evitar repetições.

**Nota:** Edite o caminho da pasta de destino no código se necessário. 
