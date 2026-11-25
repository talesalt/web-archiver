# web-archiver

Skill para o [Claude Code](https://claude.com/claude-code) que arquiva páginas **públicas** de um site (blog, wiki, fórum, documentação, site institucional) num banco SQLite incremental e exporta para Markdown, HTML ou JSONL.

Usa um Microsoft Edge aberto por você, com perfil dedicado e porta de debug local, ao qual o Selenium se anexa. Login, cookies e aceite de termos ficam com você, na janela do navegador.

## Princípios

- Respeita `robots.txt` e `Crawl-delay`; pausa aleatória de 5–8 s entre páginas.
- **Não contorna proteções**: diante de CAPTCHA, desafio anti-bot, fila de espera ou rate limit, para e salva checkpoint (ou pausa para você resolver, com `--on-gate pause`).
- Incremental: repetir o mesmo comando continua de onde parou.
- Nunca fecha o seu Edge (`driver.quit()` não é chamado).

## Instalação

```bash
git clone https://github.com/talesalt/web-archiver ~/.claude/skills/web-archiver
py -3 -m pip install --user -r ~/.claude/skills/web-archiver/requirements.txt
```

## Uso

No Claude Code, basta pedir: *"arquiva a documentação em https://site.example/docs/ e exporta em markdown"*. Pelo terminal:

```powershell
& "$HOME\.claude\skills\web-archiver\scripts\launch_edge.bat" "https://site.example/"
```

```bash
S=~/.claude/skills/web-archiver/scripts
py -3 $S/web_archive.py check
py -3 $S/web_archive.py --db site.db crawl --seed https://site.example/docs/ --max-depth 3 --max-pages 200
py -3 $S/web_archive.py --db site.db status
py -3 $S/web_archive.py --db site.db export --out md --format md
```

Veja [SKILL.md](SKILL.md) para todas as opções do `crawl` e o esquema do banco.

## Licença

MIT
