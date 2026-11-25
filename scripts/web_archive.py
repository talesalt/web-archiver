"""
web_archive.py - arquivador generico de sites publicos.

Pipeline: Edge aberto manualmente com porta de debug (launch_edge.bat)
-> Selenium anexa via debuggerAddress -> crawl educado (robots.txt, Crawl-delay,
pausa aleatoria) -> SQLite incremental -> export.

Nunca tenta contornar desafios anti-bot, CAPTCHA ou rate limit: ao detectar um,
pausa (modo interativo) ou para com checkpoint salvo (modo nao interativo).
Nunca chama driver.quit() para nao fechar o Edge do usuario.
"""
import argparse
import hashlib
import json
import random
import re
import sqlite3
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urldefrag, urljoin, urlparse, urlunparse
from urllib.robotparser import RobotFileParser

DEFAULT_DEBUGGER = "127.0.0.1:9222"
UA_TOKEN = "web-archiver"


# ---------------------------------------------------------------- browser ---

def check_debug_port(addr: str) -> dict:
    with urllib.request.urlopen(f"http://{addr}/json/version", timeout=3) as r:
        return json.loads(r.read().decode("utf-8"))


def attach_edge(addr: str):
    from selenium import webdriver
    from selenium.webdriver.edge.options import Options as EdgeOptions

    info = check_debug_port(addr)
    if not info.get("webSocketDebuggerUrl"):
        raise RuntimeError(f"{addr} sem webSocketDebuggerUrl. Relance o Edge com launch_edge.bat.")
    opts = EdgeOptions()
    opts.add_experimental_option("debuggerAddress", addr)
    return webdriver.Edge(options=opts)  # NAO chamar driver.quit()


GATE_SIGNS = [
    ("title", r"just a moment|attention required|access denied|retry later|too many requests", "desafio/bloqueio no titulo"),
    ("html", r"cf-turnstile|challenge-running|cf-chl-|g-recaptcha|h-captcha|hcaptcha", "CAPTCHA/desafio anti-bot"),
    ("html", r"rate limit(ed)?|too many requests|you are seeing this page because our systems think you may be a robot", "rate limit"),
    ("html", r"waiting room", "fila de espera"),
]


def detect_gate(html: str, title: str) -> Optional[str]:
    head = html[:20000].lower()
    t = (title or "").lower()
    for where, pat, why in GATE_SIGNS:
        if re.search(pat, t if where == "title" else head):
            return why
    return None


def fetch(driver, url: str, ready_css: str, timeout: int, on_gate: str) -> tuple[str, str]:
    from selenium.common.exceptions import TimeoutException
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.support.ui import WebDriverWait

    driver.get(url)
    while True:
        try:
            WebDriverWait(driver, timeout).until(EC.presence_of_element_located((By.CSS_SELECTOR, ready_css)))
            gate = detect_gate(driver.page_source, driver.title)
        except TimeoutException:
            gate = detect_gate(driver.page_source, driver.title) or f"timeout {timeout}s esperando '{ready_css}'"
        if not gate:
            return driver.page_source, driver.title
        print(f"PAUSA {gate} | {driver.current_url}", flush=True)
        if on_gate == "stop" or not sys.stdin.isatty():
            raise GateStop(gate)
        r = input("Resolva na janela do Edge (ou espere o limite) e ENTER; 'q' encerra: ").strip().lower()
        if r == "q":
            raise GateStop(gate)
        if detect_gate(driver.page_source, driver.title):
            driver.get(url)


class GateStop(Exception):
    pass


# ---------------------------------------------------------------- storage ---

SCHEMA = """
CREATE TABLE IF NOT EXISTS pages (
    url TEXT PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'pending',   -- pending, done, error, robots, gate
    depth INTEGER NOT NULL DEFAULT 0,
    parent TEXT,
    discovered_at TEXT NOT NULL,
    fetched_at TEXT,
    title TEXT,
    html TEXT,
    text TEXT,
    sha1 TEXT,
    links_json TEXT,
    error TEXT
);
CREATE INDEX IF NOT EXISTS ix_pages_status ON pages(status, depth);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def open_db(path: str) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(path)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL;")
    c.executescript(SCHEMA)
    return c


def enqueue(db, url: str, depth: int, parent: Optional[str]) -> bool:
    cur = db.execute(
        "INSERT OR IGNORE INTO pages(url, depth, parent, discovered_at) VALUES (?,?,?,?)",
        (url, depth, parent, now()),
    )
    return cur.rowcount > 0


# ------------------------------------------------------------------ scope ---

def normalize(url: str, keep_query: bool) -> str:
    url, _ = urldefrag(url)
    p = urlparse(url)
    if p.scheme not in ("http", "https"):
        return ""
    query = p.query if keep_query else ""
    path = p.path or "/"
    return urlunparse((p.scheme, p.netloc.lower(), path, "", query, ""))


class Scope:
    def __init__(self, seeds, mode, include, exclude):
        self.mode = mode
        self.hosts = {urlparse(s).netloc.lower() for s in seeds}
        self.prefixes = [s for s in seeds]
        self.include = [re.compile(p) for p in include]
        self.exclude = [re.compile(p) for p in exclude]

    def ok(self, url: str) -> bool:
        host = urlparse(url).netloc.lower()
        if self.mode == "domain" and host not in self.hosts:
            return False
        if self.mode == "prefix" and not any(url.startswith(p) for p in self.prefixes):
            return False
        if self.include and not any(r.search(url) for r in self.include):
            return False
        if any(r.search(url) for r in self.exclude):
            return False
        return True


class Robots:
    """robots.txt lido pelo proprio navegador (mesma sessao), cacheado por host."""

    def __init__(self, driver, ignore: bool):
        self.driver, self.ignore, self.cache = driver, ignore, {}

    def get(self, url: str) -> Optional[RobotFileParser]:
        p = urlparse(url)
        host = f"{p.scheme}://{p.netloc}"
        if host not in self.cache:
            rp = RobotFileParser()
            try:
                self.driver.get(host + "/robots.txt")
                body = self.driver.execute_script("return document.body ? document.body.innerText : ''")
                rp.parse(body.splitlines())
            except Exception:
                rp.parse([])
            self.cache[host] = rp
        return self.cache[host]

    def allowed(self, url: str) -> bool:
        return self.ignore or self.get(url).can_fetch(UA_TOKEN, url)

    def crawl_delay(self, url: str) -> float:
        if self.ignore:
            return 0.0
        d = self.get(url).crawl_delay(UA_TOKEN)
        return float(d or 0)


# ---------------------------------------------------------------- extract ---

def extract_text(html: str, content_css: Optional[str]) -> str:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    for t in soup(["script", "style", "noscript", "template"]):
        t.decompose()
    node = None
    if content_css:
        node = soup.select_one(content_css)
    node = node or soup.find("main") or soup.find("article") or soup.body or soup
    text = node.get_text("\n", strip=True)
    return re.sub(r"\n{3,}", "\n\n", text)


def page_links(driver) -> list[str]:
    return driver.execute_script("return Array.from(document.links, a => a.href);") or []


# --------------------------------------------------------------- commands ---

def cmd_check(a):
    info = check_debug_port(a.debugger)
    print("DevTools OK:", info.get("Browser"))
    d = attach_edge(a.debugger)
    for h in d.window_handles:
        d.switch_to.window(h)
        print("  aba:", d.current_url)


def cmd_crawl(a):
    db = open_db(a.db)
    seeds = [normalize(s, a.keep_query) for s in a.seed]
    for s in seeds:
        enqueue(db, s, 0, None)
    db.execute("INSERT OR REPLACE INTO meta VALUES ('last_args', ?)", (json.dumps(vars(a), default=str),))
    db.commit()

    scope = Scope(seeds, a.scope, a.include, a.exclude)
    driver = attach_edge(a.debugger)
    driver.switch_to.new_window("tab")
    robots = Robots(driver, a.ignore_robots)
    html_dir = Path(a.save_html) if a.save_html else None
    if html_dir:
        html_dir.mkdir(parents=True, exist_ok=True)

    done = 0
    try:
        while not a.max_pages or done < a.max_pages:
            row = db.execute(
                "SELECT url, depth FROM pages WHERE status='pending' AND depth<=? ORDER BY depth, rowid LIMIT 1",
                (a.max_depth,),
            ).fetchone()
            if not row:
                print("FIM fila vazia")
                break
            url, depth = row["url"], row["depth"]
            if not robots.allowed(url):
                db.execute("UPDATE pages SET status='robots' WHERE url=?", (url,))
                db.commit()
                print(f"ROBOTS {url}")
                continue

            delay = max(random.uniform(a.min_delay, a.max_delay), robots.crawl_delay(url))
            if done:
                time.sleep(delay)
            try:
                html, title = fetch(driver, url, a.ready_css, a.timeout, a.on_gate)
            except GateStop as e:
                db.execute("UPDATE pages SET error=? WHERE url=?", (f"gate: {e}", url))
                db.commit()
                print(f"PARADO {e}. Checkpoint salvo; rode o mesmo comando depois para continuar.")
                return
            except Exception as e:
                db.execute("UPDATE pages SET status='error', error=? WHERE url=?", (repr(e)[:500], url))
                db.commit()
                print(f"ERRO {url} {e!r}"[:300])
                continue

            links = []
            new = 0
            if depth < a.max_depth:
                for href in page_links(driver):
                    n = normalize(urljoin(url, href), a.keep_query)
                    if n and scope.ok(n):
                        links.append(n)
                        new += enqueue(db, n, depth + 1, url)
            text = extract_text(html, a.content_css)
            db.execute(
                "UPDATE pages SET status='done', fetched_at=?, title=?, html=?, text=?, sha1=?, links_json=?, error=NULL WHERE url=?",
                (now(), title, None if a.no_html else html, text,
                 hashlib.sha1(html.encode("utf-8", "ignore")).hexdigest(),
                 json.dumps(sorted(set(links))), url),
            )
            db.commit()
            if html_dir:
                (html_dir / (hashlib.sha1(url.encode()).hexdigest()[:16] + ".html")).write_text(html, encoding="utf-8")
            done += 1
            print(f"OK d={depth} +{new} {url} | {title[:60]}", flush=True)
    finally:
        db.commit()
        db.close()  # Edge continua aberto de proposito
    print(f"RESUMO {done} paginas nesta execucao")


def cmd_status(a):
    db = open_db(a.db)
    for r in db.execute("SELECT status, COUNT(*) n FROM pages GROUP BY status ORDER BY status"):
        print(f"{r['status']:8s} {r['n']:,}")
    for r in db.execute("SELECT url, error FROM pages WHERE error IS NOT NULL ORDER BY rowid DESC LIMIT 5"):
        print("  erro:", r["url"], "|", r["error"][:120])


def cmd_export(a):
    db = open_db(a.db)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    rows = db.execute("SELECT url, title, fetched_at, text, html FROM pages WHERE status='done' ORDER BY rowid")
    n = 0
    if a.format == "jsonl":
        with (out / "pages.jsonl").open("w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps({k: r[k] for k in ("url", "title", "fetched_at", "text")}, ensure_ascii=False) + "\n")
                n += 1
    else:
        for r in rows:
            slug = re.sub(r"[^a-zA-Z0-9]+", "_", urlparse(r["url"]).path).strip("_")[:80] or "index"
            name = f"{n:05d}_{slug}"
            if a.format == "md":
                body = f"# {r['title'] or r['url']}\n\n<{r['url']}> — arquivado em {r['fetched_at']}\n\n{r['text'] or ''}\n"
                (out / f"{name}.md").write_text(body, encoding="utf-8")
            elif r["html"]:
                (out / f"{name}.html").write_text(r["html"], encoding="utf-8")
            n += 1
    print(f"RESUMO exportadas {n} paginas em {out}")


def main():
    p = argparse.ArgumentParser(description="Arquivador generico de sites publicos (Edge + Selenium attach)")
    p.add_argument("--debugger", default=DEFAULT_DEBUGGER)
    p.add_argument("--db", default="archive/archive.db")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("check")

    c = sub.add_parser("crawl")
    c.add_argument("--seed", action="append", required=True, help="URL inicial (repetivel)")
    c.add_argument("--scope", choices=["prefix", "domain", "seeds"], default="prefix",
                   help="prefix: so URLs que comecam com uma seed; domain: mesmo host; seeds: nao segue links")
    c.add_argument("--include", action="append", default=[], help="regex que a URL precisa casar")
    c.add_argument("--exclude", action="append", default=[], help="regex que descarta a URL")
    c.add_argument("--max-depth", type=int, default=2)
    c.add_argument("--max-pages", type=int, default=50, help="0 = sem limite")
    c.add_argument("--min-delay", type=float, default=5.0)
    c.add_argument("--max-delay", type=float, default=8.0)
    c.add_argument("--timeout", type=int, default=25)
    c.add_argument("--ready-css", default="body")
    c.add_argument("--content-css", default=None, help="seletor do conteudo principal para o texto")
    c.add_argument("--keep-query", action="store_true", help="mantem ?query nas URLs (paginacao por ?page=)")
    c.add_argument("--no-html", action="store_true", help="guarda so o texto")
    c.add_argument("--save-html", default=None, help="pasta para copia do HTML bruto")
    c.add_argument("--on-gate", choices=["stop", "pause"], default="stop")
    c.add_argument("--ignore-robots", action="store_true", help="so para sites proprios/autorizados")

    sub.add_parser("status")

    e = sub.add_parser("export")
    e.add_argument("--out", required=True)
    e.add_argument("--format", choices=["md", "html", "jsonl"], default="md")

    a = p.parse_args()
    if a.cmd == "crawl" and a.scope == "seeds":
        a.max_depth = 0
    {"check": cmd_check, "crawl": cmd_crawl, "status": cmd_status, "export": cmd_export}[a.cmd](a)


if __name__ == "__main__":
    main()
