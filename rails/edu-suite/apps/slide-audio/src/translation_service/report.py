import json
from datetime import datetime
from pathlib import Path


# ---------------------------------------------------------------------------
# Text report
# ---------------------------------------------------------------------------

def write_report(output_dir: str, pdf_name: str, stats: dict, duration_seconds: float) -> None:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    mins, secs = divmod(int(duration_seconds), 60)
    duration_str = f"{mins}m {secs}s" if mins else f"{secs}s"

    lines = [
        "Translation Service — Processing Report",
        "=" * 40,
        f"Deck:      {pdf_name}",
        f"Date:      {datetime.now().strftime('%m/%d/%Y %H:%M')}",
        f"Duration:  {duration_str}",
        "",
        f"Generated: {stats['generated']} / {stats['total']} content slides",
        f"Skipped:   {stats['skipped_headers']} headers, "
        f"{stats['skipped_empty']} empty, "
        f"{stats['skipped_dupes']} duplicates",
    ]

    if stats.get("cached"):
        lines.append(f"Cached:    {stats['cached']} slides (audio already up to date)")

    lines.append("")
    if stats.get("errors"):
        lines.append(f"Errors ({len(stats['errors'])}):")
        for num, title, msg in stats["errors"]:
            lines.append(f"  slide {num} ({title}): {msg}")
    else:
        lines.append("Errors:    None")

    lines += ["", f"Output:    {out.resolve()}"]

    report_path = out / "_report.txt"
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"  Report  → {report_path}")


# ---------------------------------------------------------------------------
# HTML player
# ---------------------------------------------------------------------------

def write_html_player(output_dir: str, pdf_name: str, slides: list[dict]) -> None:
    """
    Write index.html — a two-panel interactive audio player.

    Left panel: scrollable nav with slide thumbnails grouped by week.
    Right panel: full slide image + sentence-level karaoke text + audio controls.

    slides: list of dicts with keys:
      week, slide_number, title, wav_path, image, thumb, segments
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    date_str  = datetime.now().strftime("%m/%d/%Y")
    total     = len(slides)
    slides_js = json.dumps(slides, ensure_ascii=False)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_esc(pdf_name)}</title>
<style>
/* ── Reset ─────────────────────────────────────────────────────────────── */
*, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
html, body {{ height: 100%; overflow: hidden; }}
body {{
  display: flex;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  background: #0d1117;
  color: #e6edf3;
}}

/* ── Left nav ───────────────────────────────────────────────────────────── */
#nav {{
  width: 230px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  background: #161b22;
  border-right: 1px solid #21262d;
}}
#nav-header {{
  padding: 14px 12px 12px;
  border-bottom: 1px solid #21262d;
  flex-shrink: 0;
}}
#nav-header h1 {{
  font-size: 0.78em;
  font-weight: 600;
  color: #8b949e;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  letter-spacing: 0.02em;
  text-transform: uppercase;
}}
#nav-header p {{
  font-size: 0.7em;
  color: #484f58;
  margin-top: 3px;
}}
#nav-list {{
  flex: 1;
  overflow-y: auto;
  padding: 6px 0 12px;
  scrollbar-width: thin;
  scrollbar-color: #30363d transparent;
}}
.week-label {{
  font-size: 0.67em;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.1em;
  color: #484f58;
  padding: 12px 12px 5px;
}}
.nav-item {{
  display: flex;
  align-items: center;
  gap: 9px;
  padding: 6px 8px;
  margin: 1px 6px;
  border-radius: 6px;
  cursor: pointer;
  transition: background 0.12s;
  user-select: none;
}}
.nav-item:hover {{ background: #1f2937; }}
.nav-item.active {{ background: #1d2d44; outline: 1px solid #2d4a6e; }}
.nav-thumb {{
  width: 54px;
  height: 40px;
  object-fit: cover;
  border-radius: 3px;
  flex-shrink: 0;
  background: #21262d;
}}
.nav-title {{
  font-size: 0.78em;
  line-height: 1.35;
  color: #c9d1d9;
}}
.nav-item.active .nav-title {{ color: #79c0ff; }}

/* ── Main area ──────────────────────────────────────────────────────────── */
#main {{
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  min-width: 0;
}}

/* Slide image */
#image-wrap {{
  flex: 1;
  overflow: hidden;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #010409;
  position: relative;
}}
#slide-img {{
  max-width: 100%;
  max-height: 100%;
  display: block;
  object-fit: contain;
}}
#slide-placeholder {{
  display: none;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 10px;
  color: #484f58;
  font-size: 1em;
}}

/* ── Bottom panel ───────────────────────────────────────────────────────── */
#bottom {{
  flex-shrink: 0;
  background: #161b22;
  border-top: 1px solid #21262d;
  padding: 12px 18px 14px;
}}

/* Text display — EN left, ES right */
#text-row {{
  display: flex;
  gap: 28px;
  margin-bottom: 12px;
  min-height: 72px;
  align-items: flex-start;
}}
.lang-col {{ flex: 1; min-width: 0; }}
.lang-label {{
  font-size: 0.62em;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.12em;
  color: #484f58;
  margin-bottom: 6px;
}}
.seg {{
  display: inline-block;
  padding: 4px 10px;
  border-radius: 20px;
  margin: 2px 3px;
  font-size: 0.93em;
  line-height: 1.5;
  color: #8b949e;
  transition: background 0.18s, color 0.18s, transform 0.15s;
  cursor: default;
}}
.seg.term {{
  font-size: 1.1em;
  font-weight: 700;
  color: #c9d1d9;
}}

/* Active states — blue for EN, purple for ES */
.seg.active-en {{
  background: #1f4068;
  color: #79c0ff;
  transform: scale(1.04);
  animation: pop 0.18s ease;
}}
.seg.active-en.term {{
  background: #1f4068;
  color: #58a6ff;
}}
.seg.active-es {{
  background: #2d1f5e;
  color: #d2a8ff;
  transform: scale(1.04);
  animation: pop 0.18s ease;
}}
.seg.active-es.term {{
  background: #2d1f5e;
  color: #bc8cff;
}}

@keyframes pop {{
  0%   {{ transform: scale(0.96); }}
  60%  {{ transform: scale(1.06); }}
  100% {{ transform: scale(1.04); }}
}}

/* Audio controls row */
#controls-row {{
  display: flex;
  align-items: center;
  gap: 10px;
}}
.ctrl-btn {{
  flex-shrink: 0;
  width: 34px;
  height: 34px;
  background: #21262d;
  border: 1px solid #30363d;
  color: #8b949e;
  border-radius: 8px;
  cursor: pointer;
  font-size: 1em;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: background 0.12s, color 0.12s;
  user-select: none;
}}
.ctrl-btn:hover {{ background: #2d333b; color: #e6edf3; }}
.ctrl-btn:disabled {{ opacity: 0.3; cursor: default; }}
#slide-counter {{
  flex-shrink: 0;
  font-size: 0.72em;
  color: #484f58;
  min-width: 52px;
  text-align: center;
}}
audio {{
  flex: 1;
  height: 34px;
  min-width: 0;
  accent-color: #1f6feb;
}}
</style>
</head>
<body>

<!-- Left nav -->
<div id="nav">
  <div id="nav-header">
    <h1 id="deck-title"></h1>
    <p id="deck-meta"></p>
  </div>
  <div id="nav-list"></div>
</div>

<!-- Main area -->
<div id="main">
  <div id="image-wrap">
    <img id="slide-img" src="" alt="" style="display:none">
    <div id="slide-placeholder">
      <div style="font-size:2em">&#128247;</div>
      <div id="placeholder-text">No image</div>
    </div>
  </div>
  <div id="bottom">
    <div id="text-row">
      <div class="lang-col">
        <div class="lang-label">English</div>
        <div id="en-text"></div>
      </div>
      <div class="lang-col">
        <div class="lang-label">Espa&#241;ol</div>
        <div id="es-text"></div>
      </div>
    </div>
    <div id="controls-row">
      <button class="ctrl-btn" id="btn-prev" title="Previous (&#8592;)">&#8592;</button>
      <button class="ctrl-btn" id="btn-next" title="Next (&#8594;)">&#8594;</button>
      <span id="slide-counter"></span>
      <audio id="player" controls></audio>
    </div>
  </div>
</div>

<script>
const SLIDES = {slides_js};
const TITLE  = {json.dumps(pdf_name)};
let current  = -1;

// ── Init ──────────────────────────────────────────────────────────────────
document.getElementById('deck-title').textContent = TITLE;
document.getElementById('deck-meta').textContent  = `{total} slides · {date_str}`;

// ── Build nav ─────────────────────────────────────────────────────────────
(function buildNav() {{
  const list = document.getElementById('nav-list');
  let lastWeek = -1;
  SLIDES.forEach((s, i) => {{
    if (s.week !== lastWeek) {{
      lastWeek = s.week;
      const wl = document.createElement('div');
      wl.className = 'week-label';
      wl.textContent = `Week ${{s.week}}`;
      list.appendChild(wl);
    }}
    const item = document.createElement('div');
    item.className = 'nav-item';
    item.id = `nav-${{i}}`;
    const img = document.createElement('img');
    img.className = 'nav-thumb';
    img.src = s.thumb || '';
    img.alt = '';
    img.onerror = () => {{ img.style.display = 'none'; }};
    const title = document.createElement('div');
    title.className = 'nav-title';
    title.textContent = s.title;
    item.appendChild(img);
    item.appendChild(title);
    item.addEventListener('click', () => go(i));
    list.appendChild(item);
  }});
}})();

// ── Load a slide ──────────────────────────────────────────────────────────
function go(i) {{
  if (i < 0 || i >= SLIDES.length) return;

  const player = document.getElementById('player');
  player.pause();

  current = i;
  const s = SLIDES[i];

  // Image
  const img  = document.getElementById('slide-img');
  const ph   = document.getElementById('slide-placeholder');
  if (s.image) {{
    img.src = s.image;
    img.style.display = '';
    ph.style.display  = 'none';
  }} else {{
    img.style.display = 'none';
    ph.style.display  = 'flex';
    document.getElementById('placeholder-text').textContent = s.title;
  }}

  // Audio
  player.src = s.audio || s.wav_path || '';
  player.load();

  // Text
  const enDiv = document.getElementById('en-text');
  const esDiv = document.getElementById('es-text');
  enDiv.innerHTML = '';
  esDiv.innerHTML = '';

  (s.segments || []).forEach((seg, idx) => {{
    if (seg.lang === 'pause') return;
    const el = document.createElement('span');
    el.className = `seg ${{seg.type || 'sentence'}}`;
    el.dataset.idx   = idx;
    el.dataset.start = seg.start;
    el.dataset.end   = seg.end;
    el.dataset.lang  = seg.lang;
    el.textContent   = seg.text;
    if (seg.lang === 'en') enDiv.appendChild(el);
    else                   esDiv.appendChild(el);
  }});

  // Nav active
  document.querySelectorAll('.nav-item').forEach((el, j) => {{
    el.classList.toggle('active', j === i);
  }});
  const activeItem = document.getElementById(`nav-${{i}}`);
  if (activeItem) activeItem.scrollIntoView({{ block: 'nearest', behavior: 'smooth' }});

  // Counter + arrows
  document.getElementById('slide-counter').textContent = `${{i + 1}} / ${{SLIDES.length}}`;
  document.getElementById('btn-prev').disabled = i === 0;
  document.getElementById('btn-next').disabled = i === SLIDES.length - 1;
}}

// ── Audio sync (karaoke highlight) ────────────────────────────────────────
document.getElementById('player').addEventListener('timeupdate', function() {{
  const t = this.currentTime;
  document.querySelectorAll('.seg').forEach(el => {{
    const start = parseFloat(el.dataset.start);
    const end   = parseFloat(el.dataset.end);
    const lang  = el.dataset.lang;
    const isActive = t >= start && t < end;
    el.classList.toggle('active-en', isActive && lang === 'en');
    el.classList.toggle('active-es', isActive && lang === 'es');
  }});
}});

// Clear highlights when audio ends or is paused/seeked to 0
document.getElementById('player').addEventListener('ended', clearHighlights);

function clearHighlights() {{
  document.querySelectorAll('.seg').forEach(el => {{
    el.classList.remove('active-en', 'active-es');
  }});
}}

// ── Keyboard shortcuts ────────────────────────────────────────────────────
document.addEventListener('keydown', e => {{
  if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
  if (e.key === 'ArrowRight') {{ e.preventDefault(); go(current + 1); }}
  else if (e.key === 'ArrowLeft')  {{ e.preventDefault(); go(current - 1); }}
  else if (e.key === ' ') {{
    e.preventDefault();
    const p = document.getElementById('player');
    p.paused ? p.play() : p.pause();
  }}
}});

document.getElementById('btn-prev').addEventListener('click', () => go(current - 1));
document.getElementById('btn-next').addEventListener('click', () => go(current + 1));

// ── Boot ──────────────────────────────────────────────────────────────────
go(0);
</script>
</body>
</html>"""

    player_path = out / "index.html"
    player_path.write_text(html, encoding="utf-8")
    print(f"  Player  → {player_path}")


def _esc(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;")
             .replace(">", "&gt;").replace('"', "&quot;"))
