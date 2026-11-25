---
name: web-archiver
description: Arquiva páginas públicas de um site (texto + HTML) num SQLite incremental, usando um Edge aberto manualmente e anexado via Selenium (porta de debug 9222), com robots.txt, Crawl-delay, pausa aleatória e parada automática diante de CAPTCHA/rate limit. Use quando o usuário pedir para arquivar, fazer backup, salvar uma cópia offline, espelhar ou raspar páginas públicas de um blog, wiki, fórum, documentação ou site institucional, e exportar para Markdown, HTML ou JSONL. Por script, gastando poucos tokens.
---

# web-archiver

Script: `scripts/web_archive.py` (Python 3 + `selenium`, `beautifulsoup4`). Não leia o HTML baixado; confie nas linhas `OK` / `ERRO` / `ROBOTS` / `PAUSA` / `PARADO` / `RESUMO`.

## Regras

- **Só conteúdo público** ou do próprio usuário. Respeita `robots.txt` e `Crawl-delay` por padrão; `--ignore-robots` apenas para site do próprio usuário ou com autorização explícita.
- **Nunca contornar proteções.** Diante de CAPTCHA, desafio anti-bot, fila de espera ou 429, o script para (`PARADO`) e salva checkpoint. Avise o usuário; não tente burlar, não reduza o delay para compensar, não troque de abordagem para escapar do bloqueio. Se o site bloqueia automação, a resposta é parar ou usar a API/exportação oficial do site.
- Antes de arquivar um site inteiro, leia os termos de uso e prefira API ou dump oficial quando existir.
- Pacing padrão 5–8 s por página; não abaixe de 3 s sem motivo do usuário.
- Nunca chame `driver.quit()`: o Edge é do usuário.

## Passo 1 — abrir o Edge (usuário)

```powershell
& "$HOME\.claude\skills\web-archiver\scripts\launch_edge.bat" "https://site.example/"
```

Perfil dedicado em `%LOCALAPPDATA%\web-archiver\edge-profile` (não mexe no Edge normal). Login, cookies e aceite de termos são feitos **pelo usuário** na janela.

## Passo 2 — rodar

```bash
S=~/.claude/skills/web-archiver/scripts
py -3 $S/web_archive.py check                                       # confere a porta 9222
py -3 $S/web_archive.py --db PASTA/site.db crawl --seed https://site.example/docs/ --max-depth 3 --max-pages 200
py -3 $S/web_archive.py --db PASTA/site.db status
py -3 $S/web_archive.py --db PASTA/site.db export --out PASTA/md --format md   # md | html | jsonl
```

Rode `crawl` em segundo plano para lotes longos. É incremental: repetir o mesmo comando continua a fila de onde parou.

### Opções do `crawl`

| opção | efeito |
|---|---|
| `--seed URL` | URL inicial (repetível) |
| `--scope prefix\|domain\|seeds` | `prefix` (padrão): só URLs começando por uma seed; `domain`: mesmo host; `seeds`: só as seeds |
| `--include RE` / `--exclude RE` | filtros regex de URL (repetíveis) |
| `--max-depth N` / `--max-pages N` | limites (`--max-pages 0` = sem limite) |
| `--min-delay` / `--max-delay` | pausa aleatória entre páginas (s) |
| `--ready-css SEL` | espera esse seletor antes de salvar |
| `--content-css SEL` | seletor do conteúdo principal para o texto |
| `--keep-query` | mantém `?query` (paginação `?page=`) |
| `--no-html` / `--save-html DIR` | só texto / cópia do HTML em pasta |
| `--on-gate pause` | em terminal interativo, pausa para o usuário resolver na janela em vez de parar |

## Banco

Tabela `pages(url, status, depth, parent, fetched_at, title, html, text, sha1, links_json, error)`; `status` ∈ `pending`, `done`, `error`, `robots`. Consultas próprias: `sqlite3 PASTA/site.db "select url,title from pages where status='done'"`.
