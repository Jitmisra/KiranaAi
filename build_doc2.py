#!/usr/bin/env python3
"""Render DECISION.md into a designed, navigable single-file HTML artifact."""
import re, html, pathlib, markdown

import sys
SRC = pathlib.Path(sys.argv[1] if len(sys.argv)>1 else "DECISION.md")
OUT = SRC.with_suffix(".html")

md_text = SRC.read_text(encoding="utf-8")
md_text = md_text.replace("- [ ] ", "- ☐ ")

body = markdown.markdown(
    md_text,
    extensions=["tables", "fenced_code", "attr_list", "sane_lists"],
)

# Blockquotes carrying a warning glyph become red-ruled flags.
body = re.sub(r'<blockquote>\s*<p>(\s*(?:⚠|<strong>⚠))',
              r'<blockquote class="flag"><p>\1', body)

# Every table gets its own horizontal scroll rail.
body = re.sub(r'<table>', '<div class="rail"><table>', body)
body = re.sub(r'</table>', '</table></div>', body)

# Section ids + sidebar entries from the numbered h2s.
nav = []
def slug_h2(m):
    raw = re.sub(r'<[^>]+>', '', m.group(1)).strip()
    num = re.match(r'(\d+)\.\s*(.*)', raw)
    if num:
        n, title = num.group(1), num.group(2)
    else:
        n, title = "", raw
    sid = "s" + (n if n else re.sub(r'\W+', '-', raw.lower())[:24])
    nav.append((n, title, sid))
    return (f'<h2 id="{sid}"><span class="secnum">{html.escape(n)}</span>'
            f'<span class="sectitle">{html.escape(title)}</span></h2>')
body = re.sub(r'<h2>(.*?)</h2>', slug_h2, body, flags=re.S)

nav_html = "\n".join(
    f'<a href="#{sid}"><span class="n">{html.escape(n) or "·"}</span>'
    f'<span class="t">{html.escape(t)}</span></a>'
    for n, t, sid in nav
)

CSS = """
*,*::before,*::after{box-sizing:border-box}

/* ---- light palette: ledger stock, defined on bare :root ---- */
:root{
  --paper:#F7F5F0; --card:#FFFDF8; --ink:#1A1C22; --ink-soft:#3A3D45;
  --muted:#6E6A62; --rule:#DDD8CC; --rule-soft:#E9E5DA;
  --stop:#A02C1E; --stop-bg:#F6E9E5; --verify:#2D5A4A; --verify-bg:#E7EFEA;
  --code-bg:#F1EEE6; --sel:#E4DECE;
  --shadow:0 1px 2px rgba(26,28,34,.05),0 8px 24px -16px rgba(26,28,34,.22);
}
@media (prefers-color-scheme:dark){
  :root:not([data-theme="light"]){
    --paper:#14161A; --card:#1A1D22; --ink:#E6E2D9; --ink-soft:#C3BFB5;
    --muted:#8E8A81; --rule:#2C3037; --rule-soft:#23262C;
    --stop:#E07964; --stop-bg:#2A1D1A; --verify:#7FBBA1; --verify-bg:#182521;
    --code-bg:#1E2127; --sel:#33383F;
    --shadow:0 1px 2px rgba(0,0,0,.4),0 8px 24px -16px rgba(0,0,0,.7);
  }
}
:root[data-theme="dark"]{
  --paper:#14161A; --card:#1A1D22; --ink:#E6E2D9; --ink-soft:#C3BFB5;
  --muted:#8E8A81; --rule:#2C3037; --rule-soft:#23262C;
  --stop:#E07964; --stop-bg:#2A1D1A; --verify:#7FBBA1; --verify-bg:#182521;
  --code-bg:#1E2127; --sel:#33383F;
  --shadow:0 1px 2px rgba(0,0,0,.4),0 8px 24px -16px rgba(0,0,0,.7);
}

--sans:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;

html{-webkit-text-size-adjust:100%}
body{
  margin:0; background:var(--paper); color:var(--ink);
  font-family:ui-serif,Georgia,"Iowan Old Style","Times New Roman",serif;
  font-size:17px; line-height:1.65; overflow-x:hidden;
  font-synthesis-weight:none;
}
::selection{background:var(--sel)}

.wrap{display:grid; grid-template-columns:264px minmax(0,1fr); gap:0; max-width:1360px; margin:0 auto}

/* ---------- sidebar ---------- */
nav.toc{
  position:sticky; top:0; align-self:start; height:100dvh; overflow-y:auto;
  border-right:1px solid var(--rule); padding:34px 20px 48px 24px;
  display:flex; flex-direction:column; gap:2px;
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
}
nav.toc .brand{margin-bottom:18px; padding-bottom:16px; border-bottom:1px solid var(--rule)}
nav.toc .brand b{
  display:block; font-size:20px; font-weight:800; letter-spacing:-.02em; color:var(--ink);
}
nav.toc .brand span{
  display:block; margin-top:5px; font-size:11px; line-height:1.45; color:var(--muted);
  font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
}
nav.toc a{
  display:flex; gap:9px; align-items:baseline; text-decoration:none;
  padding:6px 9px; border-radius:5px; color:var(--ink-soft);
  font-size:12.5px; line-height:1.35;
}
nav.toc a .n{
  font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
  font-size:11px; color:var(--muted); min-width:15px; font-variant-numeric:tabular-nums;
}
nav.toc a:hover{background:var(--rule-soft); color:var(--ink)}
nav.toc a:focus-visible{outline:2px solid var(--stop); outline-offset:1px}
nav.toc a.active{background:var(--stop-bg); color:var(--stop)}
nav.toc a.active .n{color:var(--stop)}

/* ---------- main ---------- */
main{padding:56px 56px 160px; min-width:0}
.doc{max-width:74ch}

h1{
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
  font-size:clamp(30px,4.4vw,44px); font-weight:850; letter-spacing:-.035em;
  line-height:1.06; margin:0 0 6px; text-wrap:balance;
}
h1 + h3{
  font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
  font-size:12px; font-weight:500; letter-spacing:.02em; color:var(--muted);
  margin:0 0 40px; padding-bottom:26px; border-bottom:2px solid var(--ink);
  text-transform:none;
}
h2{
  display:flex; gap:14px; align-items:baseline; scroll-margin-top:22px;
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
  font-size:clamp(21px,2.5vw,27px); font-weight:800; letter-spacing:-.025em;
  line-height:1.15; margin:64px 0 20px; padding-top:22px; border-top:1px solid var(--rule);
  text-wrap:balance;
}
h2 .secnum{
  font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
  font-size:13px; font-weight:600; color:var(--stop); letter-spacing:0;
  font-variant-numeric:tabular-nums; padding-top:.35em;
}
h3{
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
  font-size:16.5px; font-weight:750; letter-spacing:-.012em; line-height:1.3;
  margin:36px 0 12px; color:var(--ink); text-wrap:balance;
}
h4{
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
  font-size:14.5px; font-weight:700; margin:28px 0 10px; letter-spacing:-.008em;
}
p{margin:0 0 15px}
strong{font-weight:700; color:var(--ink)}
em{font-style:italic}
hr{border:0; border-top:1px solid var(--rule-soft); margin:34px 0}
a{color:var(--stop); text-decoration:underline; text-underline-offset:2px;
  text-decoration-thickness:1px; text-decoration-color:color-mix(in srgb,var(--stop) 35%,transparent)}
a:hover{text-decoration-color:var(--stop)}
a:focus-visible{outline:2px solid var(--stop); outline-offset:2px; border-radius:2px}

ul,ol{margin:0 0 16px; padding-left:22px}
li{margin:0 0 7px}
li::marker{color:var(--muted)}

code{
  font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
  font-size:.855em; background:var(--code-bg); padding:.13em .38em;
  border-radius:3px; border:1px solid var(--rule-soft); word-break:break-word;
}
pre{
  background:var(--code-bg); border:1px solid var(--rule); border-radius:7px;
  padding:15px 17px; overflow-x:auto; margin:0 0 20px; box-shadow:var(--shadow);
}
pre code{background:none; border:0; padding:0; font-size:12.5px; line-height:1.55;
  white-space:pre; color:var(--ink-soft)}

blockquote{
  margin:0 0 20px; padding:13px 18px; border-left:3px solid var(--rule);
  background:var(--card); border-radius:0 6px 6px 0; color:var(--ink-soft);
}
blockquote p:last-child{margin-bottom:0}
blockquote.flag{
  border-left-color:var(--stop); background:var(--stop-bg); color:var(--ink);
}
blockquote.flag strong{color:var(--stop)}

/* ---------- tables: mono, tabular, own scroll rail ---------- */
.rail{overflow-x:auto; margin:0 0 22px; border:1px solid var(--rule);
  border-radius:7px; background:var(--card); box-shadow:var(--shadow)}
table{border-collapse:collapse; width:100%; min-width:max-content;
  font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
  font-size:12.2px; line-height:1.5; font-variant-numeric:tabular-nums}
thead th{
  position:sticky; top:0; background:var(--card); text-align:left;
  font-weight:700; font-size:10.5px; letter-spacing:.06em; text-transform:uppercase;
  color:var(--muted); padding:10px 13px; border-bottom:1.5px solid var(--rule);
  white-space:nowrap;
}
tbody td{padding:10px 13px; border-bottom:1px solid var(--rule-soft);
  vertical-align:top; max-width:46ch}
tbody tr:last-child td{border-bottom:0}
tbody tr:hover{background:var(--rule-soft)}
table code{font-size:.95em; background:transparent; border:0; padding:0}
table strong{font-weight:700}

/* section 0 — the verification layer, marked as a correction */
#s0{border-top-color:var(--stop)}

@media (max-width:940px){
  .wrap{grid-template-columns:1fr}
  nav.toc{position:static; height:auto; border-right:0; border-bottom:1px solid var(--rule);
    flex-direction:row; flex-wrap:wrap; gap:3px; padding:20px}
  nav.toc .brand{flex:0 0 100%; margin-bottom:8px}
  nav.toc a{padding:5px 8px}
  nav.toc a .t{display:none}
  nav.toc a .n{min-width:0}
  main{padding:32px 22px 110px}
}
@media (prefers-reduced-motion:reduce){*{animation:none!important; transition:none!important}}
"""
# strip the stray custom-prop line that isn't valid at top level
CSS = CSS.replace('--sans:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;\n', '')

JS = """
(function(){
  var links=[].slice.call(document.querySelectorAll('nav.toc a'));
  var secs=links.map(function(a){return document.querySelector(a.getAttribute('href'))}).filter(Boolean);
  function sync(){
    var y=window.scrollY+120,cur=0;
    for(var i=0;i<secs.length;i++){ if(secs[i].offsetTop<=y) cur=i; }
    links.forEach(function(a,i){ a.classList.toggle('active',i===cur); });
  }
  var t=null;
  window.addEventListener('scroll',function(){ if(t)return; t=requestAnimationFrame(function(){t=null;sync()}); },{passive:true});
  sync();
})();
"""

OUT.write_text(
    f'<title>Rukja — Razorpay Buildathon decision document</title>\n'
    f'<meta name="viewport" content="width=device-width,initial-scale=1">\n'
    f'<style>{CSS}</style>\n'
    f'<div class="wrap">\n'
    f'<nav class="toc"><div class="brand"><b>Rukja</b>'
    f'<span>decision doc · 40 agents · 6.3M tok<br>Razorpay AI Buildathon</span></div>\n'
    f'{nav_html}\n</nav>\n'
    f'<main><div class="doc">{body}</div></main>\n'
    f'</div>\n<script>{JS}</script>\n',
    encoding="utf-8",
)
print(f"wrote {OUT} ({OUT.stat().st_size:,} bytes), {len(nav)} sections")
