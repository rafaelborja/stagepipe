# Generates docs/pipeline.svg from an actual schedule simulation (not hand-drawn bars).
ITEMS = 6
STAGES = [("render", 1.0, 3), ("ocr", 3.0, 3), ("save", 1.0, 1)]   # name, duration per item, workers
COLORS = ["#2563eb", "#16a34a", "#d97706", "#db2777", "#7c3aed", "#0891b2"]

def simulate(barrier):
    # returns {stage: [(item, worker, start, end)]}
    ready = {i: 0.0 for i in range(ITEMS)}          # when each item is available to the next stage
    out = {}
    for name, dur, w in STAGES:
        free = [0.0] * w
        order = sorted(range(ITEMS), key=lambda i: (ready[i], i))
        if barrier and name != STAGES[0][0]:
            t0 = max(ready.values()); free = [t0] * w
        rows = []
        for i in order:
            k = min(range(w), key=lambda x: free[x])
            s = max(free[k], ready[i]); e = s + dur
            free[k] = e; rows.append((i, k, s, e)); ready[i] = e
        out[name] = rows
    return out

W, PAD, LABEL = 960, 28, 74
X0, XW = PAD + LABEL, W - 2 * PAD - LABEL
LANE, GAP = 15, 14
panels = [("Stage by stage: every stage waits for the slowest item of the previous one", True),
          ("stagepipe: an item moves on as soon as the next stage has a free worker", False)]
sims = [simulate(b) for _, b in panels]
tmax = max(e for s in sims for rows in s.values() for *_, e in rows)
sc = XW / tmax

parts, y = [], 18
def t(x, y, txt, size=13, weight=400, fill="#1f2937", anchor="start"):
    parts.append(f'<text x="{x}" y="{y}" font-size="{size}" font-weight="{weight}" fill="{fill}" text-anchor="{anchor}">{txt}</text>')

for (title, barrier), sim in zip(panels, sims):
    t(PAD, y + 12, title, 15, 700, "#111827" if not barrier else "#4b5563"); y += 30
    first = min(e for *_, e in sim["save"])
    for name, dur, w in STAGES:
        h = w * LANE
        t(PAD, y + h / 2 + 4, name, 13, 600, "#374151")
        parts.append(f'<rect x="{X0}" y="{y}" width="{XW}" height="{h}" rx="3" fill="#f3f4f6"/>')
        for i, k, s, e in sim[name]:
            parts.append(f'<rect x="{X0 + s*sc:.1f}" y="{y + k*LANE + 1}" width="{(e-s)*sc - 1.5:.1f}" height="{LANE - 2}" rx="2" fill="{COLORS[i]}"/>')
        y += h + 6
    fx = X0 + first * sc
    parts.append(f'<line x1="{fx:.1f}" y1="{y - 4}" x2="{fx:.1f}" y2="{y + 8}" stroke="#111827" stroke-width="1.5"/>')
    t(fx + 6, y + 6, "first result", 12, 600, "#111827")
    end = max(e for *_, e in sim["save"])
    t(X0 + end * sc, y + 6, f"done at t={end:.0f}", 12, 400, "#4b5563", "end")
    y += 28 + GAP
H = y + 4
legend_y = H - 6
parts.append(f'<line x1="{X0}" y1="{legend_y - 20}" x2="{X0 + XW}" y2="{legend_y - 20}" stroke="#9ca3af" stroke-width="1"/>')
t(X0 + XW, legend_y - 6, "time  →", 12, 400, "#4b5563", "end")
t(PAD, legend_y - 6, f"{ITEMS} items, one colour each. render 1 unit x3 workers, ocr 3 units x3 workers, save 1 unit x1 worker.", 12, 400, "#4b5563")
H += 12
svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" '
       f'font-family="-apple-system, Segoe UI, Helvetica, Arial, sans-serif" role="img" '
       f'aria-label="Timeline comparison: stage by stage versus stagepipe">'
       f'<title>Stage by stage versus stagepipe</title>'
       f'<rect width="{W}" height="{H}" rx="10" fill="#ffffff" stroke="#e5e7eb"/>' + "".join(parts) + "</svg>")
open(__import__("os").path.join(__import__("os").path.dirname(__file__), "pipeline.svg"), "w", encoding="utf-8", newline="\n").write(svg)
for (title, b), s in zip(panels, sims):
    print(("barrier  " if b else "pipeline"), "first result", min(e for *_, e in s["save"]), "done", max(e for *_, e in s["save"]))
