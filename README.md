# YT Downloader

Baixa músicas (MP3/M4A) e vídeos (MP4 H.264/AVI DivX-Xvid) do YouTube, com capa, artista e título no arquivo.
Desenvolvido por Victor Hugo.

| | **Free** | **Premium** |
|---|---|---|
| Onde usa | Site | Extensão (Chrome, Edge, Firefox) e site |
| Preço | Grátis | R$ 14,90 por 30 dias, sem renovação automática |
| Áudio / vídeo | até 192 kbps / 720p | até 320 kbps / 4K |
| Duração por vídeo | 15 min | 30 min |
| Quantidade | 10 por hora, por IP | 100 a cada 24 horas |
| Links por vez / playlist | 1 / não | 20 / até 100 faixas (.zip) |
| Histórico e "baixar de novo" | não | sim |

Todos esses valores mudam por variável de ambiente: veja [.env.example](.env.example).

## Rodar localmente (Windows, PowerShell)

Pré-requisitos: Python 3.10+ e Node.js 20+. O FFmpeg já vem no projeto.

```powershell
# uma vez só
py -m venv venv
.\venv\Scripts\python.exe -m pip install -r requirements.txt
.\venv\Scripts\python.exe manage.py tornar-admin seu@email.com

# toda vez
.\venv\Scripts\python.exe app.py
```

Abra http://localhost:5000. Sem e-mail configurado, o link de login aparece na própria tela (só fora de produção).
Sem token do Mercado Pago, o pagamento é simulado e nada é cobrado. O painel fica em `/admin`.

Testes: `.\venv\Scripts\python.exe -m unittest discover -s tests -v`

Terminal (uso pessoal): `.\venv\Scripts\python.exe main.py`

## Rodar com Docker (igual à produção)

```powershell
copy .env.example .env   # preencha
docker compose up -d --build
docker compose logs -f
docker compose exec app python manage.py link-login seu@email.com
```

## Publicar na internet

1. Suba o código para o GitHub.
2. No Render: **New > Blueprint** usa o [render.yaml](render.yaml). No Railway, o [railway.json](railway.json) já aponta o Dockerfile; crie um volume em `/data`. Em uma VPS, use o `docker compose` com Caddy ou Nginx para HTTPS.
3. Defina `YTD_PUBLIC_URL`, `YTD_ADMIN_EMAILS`, `YTD_SECRET_KEY`, o SMTP e o Mercado Pago.
4. No Mercado Pago, cadastre o webhook `https://seudominio/api/pagamentos/webhook` (evento Pagamentos).
5. **Entre em `/admin` > Sistema > Testar agora.** Esse teste diz se o YouTube aceita o IP do servidor. Se bloquear, as saídas são cookies de uma conta ou proxy residencial, e isso precisa ser resolvido antes de vender.

Não dá para hospedar na Vercel: o app precisa de processos longos, disco, FFmpeg e fila em segundo plano.

## Extensão

`/extensao/chrome.zip` e `/extensao/firefox.zip` já saem com o endereço do servidor configurado.

**Lojas:** a Chrome Web Store proíbe extensões que baixam vídeos do YouTube, então espere recusa. As alternativas são instalação manual (Chrome/Edge), Firefox Add-ons ou Edge Add-ons, cada uma sujeita à análise da própria loja.

## Comandos de administração

```
manage.py tornar-admin | remover-admin | link-login | cancelar  <email>
manage.py criar-premium <email> [--dias 30]
manage.py listar
manage.py atualizar-ytdlp
```

## Logs

- `data/logs/app.log`: tudo (requisições, downloads, logins, pagamentos, ações de admin); um arquivo por dia, cerca de 6 meses guardados.
- `data/logs/errors.log`: só avisos e erros.
- Também aparecem em `/admin` > Logs, com filtros.

## Atualização do yt-dlp

Verifica a cada 12 h e também quando um download falha com erro típico de mudança no YouTube. Cada download roda em processo próprio, então a versão nova vale sem reiniciar. Em `/admin` > Sistema dá para forçar.

## Estrutura

```
app.py              monta o servidor
main.py / manage.py terminal e administração
ytd/core.py         links, formatos, opções do yt-dlp, prévia
ytd/worker.py       processo que baixa um link
ytd/jobs.py         fila, progresso, .zip, limpeza
ytd/db.py           SQLite
ytd/payments.py     Mercado Pago e modo simulado
ytd/mailer.py       e-mail
ytd/updater.py      atualização do yt-dlp
ytd/logs.py         logs
ytd/routes/         pages, api, admin
templates/, static/ interface
extension/          extensão Premium
Dockerfile, docker-compose.yml, render.yaml, railway.json   deploy
```

## Escalar além de um servidor

Troque a fila em memória de `ytd/jobs.py` por Redis + RQ/Celery, o SQLite por PostgreSQL e os arquivos temporários por armazenamento compartilhado.
