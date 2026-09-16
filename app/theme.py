"""Visual system for the Meridian console.

The subject is instrumentation: a system whose argument is that it measures
itself rather than asserting. The palette is cool and technical rather than
editorial, the two signal colours appear only on genuine signal (a passing
test, a blocked query), and monospace is reserved for figures where column
alignment carries meaning -- never for labels.
"""

INK = "#16202C"        # deep slate, genuinely blue rather than tinted black
PAPER = "#F7F8FA"      # cool white
SURFACE = "#FFFFFF"
RULE = "#DCE1E8"       # hairline
MUTED = "#5A6878"
SIGNAL = "#1F6F5C"     # deep teal: pass, allowed, reconciled
ALERT = "#B3401F"      # oxide: fail, blocked, quarantined
WARN = "#9A6B1E"       # amber: warning severity

CSS = f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap');

html, body, [class*="css"], .stApp {{
    font-family: 'IBM Plex Sans', -apple-system, sans-serif;
}}

.stApp {{ background: {PAPER}; color: {INK}; }}

[data-testid="stSidebar"] {{
    background: {INK};
    border-right: none;
}}
[data-testid="stSidebar"] * {{ color: #C8D2DE; }}
[data-testid="stSidebar"] h1,
[data-testid="stSidebar"] h2,
[data-testid="stSidebar"] h3 {{ color: #FFFFFF; }}

h1, h2, h3, h4 {{
    font-family: 'IBM Plex Sans', sans-serif;
    font-weight: 600;
    letter-spacing: -0.015em;
    color: {INK};
}}
h1 {{ font-size: 1.95rem; line-height: 1.15; margin-bottom: .2rem; }}
h2 {{ font-size: 1.15rem; margin-top: 2.2rem; }}
h3 {{ font-size: .95rem; }}

p, li {{ max-width: 72ch; line-height: 1.55; }}

/* Standfirst under a page title */
.lede {{
    color: {MUTED};
    font-size: 1.02rem;
    line-height: 1.5;
    max-width: 66ch;
    margin: 0 0 1.6rem 0;
}}

/* Measurement sheet: label left, figure right, hairline between */
.sheet {{ border-top: 1px solid {RULE}; margin: .4rem 0 1.4rem 0; }}
.row {{
    display: flex; align-items: baseline; justify-content: space-between;
    gap: 1.5rem; padding: .62rem 0; border-bottom: 1px solid {RULE};
}}
.row .k {{ font-size: .9rem; color: {INK}; }}
.row .k small {{ display: block; color: {MUTED}; font-size: .78rem; margin-top: .12rem; }}
.row .v {{
    font-family: 'IBM Plex Mono', monospace;
    font-size: .95rem; font-variant-numeric: tabular-nums;
    white-space: nowrap;
}}
.v.pass {{ color: {SIGNAL}; }}
.v.fail {{ color: {ALERT}; }}
.v.warn {{ color: {WARN}; }}

/* The one big figure a page is about */
.headline {{ margin: .2rem 0 1.4rem 0; }}
.headline .fig {{
    font-family: 'IBM Plex Mono', monospace;
    font-size: 3.4rem; font-weight: 500; line-height: 1;
    letter-spacing: -0.03em; color: {INK};
    font-variant-numeric: tabular-nums;
}}
.headline .cap {{ color: {MUTED}; font-size: .88rem; margin-top: .45rem; max-width: 46ch; }}

/* Pipeline trace on the Ask page */
.trace {{ border-left: 2px solid {RULE}; padding-left: 1.1rem; margin: .5rem 0 0 .35rem; }}
.step {{ position: relative; padding-bottom: 1.25rem; }}
.step:last-child {{ padding-bottom: .2rem; }}
.step::before {{
    content: ''; position: absolute; left: -1.52rem; top: .38rem;
    width: .5rem; height: .5rem; border-radius: 50%;
    background: {RULE}; box-shadow: 0 0 0 3px {PAPER};
}}
.step.on::before {{ background: {SIGNAL}; }}
.step.off::before {{ background: {ALERT}; }}
.step .name {{ font-size: .78rem; color: {MUTED}; margin-bottom: .3rem; }}
.step .body {{ font-size: .9rem; }}

.verdict {{
    display: inline-block; font-size: .8rem; padding: .18rem .55rem;
    border: 1px solid; border-radius: 2px;
    font-family: 'IBM Plex Mono', monospace;
}}
.verdict.allow {{ color: {SIGNAL}; border-color: {SIGNAL}; }}
.verdict.block {{ color: {ALERT}; border-color: {ALERT}; }}

.sqlbox {{
    font-family: 'IBM Plex Mono', monospace; font-size: .82rem;
    background: {SURFACE}; border: 1px solid {RULE};
    padding: .75rem .9rem; white-space: pre-wrap; line-height: 1.5;
    color: {INK};
}}

.note {{
    font-size: .85rem; color: {MUTED}; border-left: 2px solid {RULE};
    padding: .1rem 0 .1rem .8rem; margin: .8rem 0; max-width: 64ch;
}}

.stButton > button {{
    background: {INK}; color: #FFFFFF; border: none; border-radius: 2px;
    font-weight: 500; font-size: .88rem; padding: .48rem 1.1rem;
}}
.stButton > button:hover {{ background: #24344a; color: #FFFFFF; }}
.stButton > button:focus-visible {{ outline: 2px solid {SIGNAL}; outline-offset: 2px; }}

[data-testid="stDataFrame"] {{ border: 1px solid {RULE}; }}
footer, #MainMenu {{ visibility: hidden; }}
.block-container {{ padding-top: 2.6rem; max-width: 1080px; }}

@media (prefers-reduced-motion: reduce) {{
    * {{ animation: none !important; transition: none !important; }}
}}
</style>
"""


def row(label: str, value: str, sub: str = "", state: str = "") -> str:
    """One line of a measurement sheet."""
    cls = f" {state}" if state else ""
    small = f"<small>{sub}</small>" if sub else ""
    return (f'<div class="row"><div class="k">{label}{small}</div>'
            f'<div class="v{cls}">{value}</div></div>')


def sheet(rows: list[str]) -> str:
    return '<div class="sheet">' + "".join(rows) + "</div>"


def headline(figure: str, caption: str) -> str:
    return (f'<div class="headline"><div class="fig">{figure}</div>'
            f'<div class="cap">{caption}</div></div>')


def lede(text: str) -> str:
    return f'<p class="lede">{text}</p>'


def note(text: str) -> str:
    return f'<div class="note">{text}</div>'
