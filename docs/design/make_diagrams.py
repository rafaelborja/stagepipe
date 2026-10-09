"""Generates the decision diagrams used by docs/design/stage-classes.md.

    python docs/design/make_diagrams.py

Writes docs/design/img/stage-hierarchy.svg, effect-matrix.svg and stop-modes.svg. They illustrate
PROPOSED designs (nothing here is implemented).
"""
import os

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "img")
os.makedirs(OUT, exist_ok=True)

INK, MUTED, LINE, WHITE = "#111827", "#4b5563", "#d1d5db", "#ffffff"
GREEN, GREEN_BG = "#15803d", "#dcfce7"
AMBER, AMBER_BG = "#b45309", "#fef3c7"
RED, RED_BG = "#b91c1c", "#fee2e2"
BLUE, BLUE_BG = "#1d4ed8", "#dbeafe"
GREY_BG = "#f3f4f6"
FONT = "-apple-system, Segoe UI, Helvetica, Arial, sans-serif"


class Svg:
    def __init__(self, w, title, desc):
        self.w, self.title, self.desc, self.parts = w, title, desc, []

    def rect(self, x, y, w, h, fill=WHITE, stroke=LINE, rx=8, sw=1.2):
        self.parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" fill="{fill}" stroke="{stroke}" stroke-width="{sw}"/>')

    def text(self, x, y, s, size=13, weight=400, fill=INK, anchor="start", italic=False):
        s = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        st = ' font-style="italic"' if italic else ""
        self.parts.append(f'<text xml:space="preserve" x="{x}" y="{y}" font-size="{size}" font-weight="{weight}" fill="{fill}" text-anchor="{anchor}"{st}>{s}</text>')

    def lines(self, x, y, items, size=12, gap=17, fill=INK, weight=400):
        for i, s in enumerate(items):
            self.text(x, y + i * gap, s, size, weight, fill)

    def line(self, x1, y1, x2, y2, stroke="#9ca3af", sw=1.5):
        self.parts.append(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{stroke}" stroke-width="{sw}"/>')

    def save(self, name, h):
        svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {self.w} {h}" width="{self.w}" height="{h}" '
               f'font-family="{FONT}" role="img" aria-label="{self.desc}"><title>{self.title}</title>'
               f'<rect width="{self.w}" height="{h}" rx="12" fill="{WHITE}" stroke="{LINE}"/>' + "".join(self.parts) + "</svg>")
        with open(os.path.join(OUT, name), "w", encoding="utf-8", newline="\n") as f:
            f.write(svg)
        print("wrote", name, f"{self.w}x{h}")


# ---------------------------------------------------------------- 1. hierarchy: one class vs base + subclasses
def hierarchy():
    s = Svg(980, "Stage design options", "Option A: one Stage class with keywords. Option B: a base Stage class with Pure, Scratch, Target and Destructive subclasses.")
    s.text(24, 34, "Decision 1: how should a stage declare what kind of work it does?", 17, 700)
    s.text(24, 56, "Proposed designs only. Nothing is implemented. Memory cost is the same: classes exist once per stage, never per item.", 12, 400, MUTED)

    # panel A
    ax, ay, aw, ah = 24, 76, 440, 492
    s.rect(ax, ay, aw, ah, GREY_BG)
    s.text(ax + 16, ay + 28, "Option A: one class, keywords", 15, 700, BLUE)
    s.rect(ax + 16, ay + 46, aw - 32, 164, WHITE, BLUE)
    s.lines(ax + 28, ay + 70, ["Stage(name, fn, workers=3,", "      effect=\"destructive\",   # pure | scratch | target | destructive",
                               "      save=True, retries=0,", "      idempotent=False,", "      recover=lambda ctx: ...)"], 12, 18)
    s.text(ax + 16, ay + 236, "The runner decides what each value of effect= means:", 12, 600)
    s.lines(ax + 16, ay + 258, ["if effect == \"destructive\": never retry; markers; ...", "elif effect == \"target\": retry only if idempotent; ...", "elif ...   (one switch, in the runner)"], 12, 17, MUTED)
    s.text(ax + 16, ay + 330, "Good", 13, 700, GREEN)
    s.lines(ax + 16, ay + 350, ["+ Smallest API: one name to learn", "+ Nothing new to import"], 12, 17)
    s.text(ax + 16, ay + 396, "Costly", 13, 700, RED)
    s.lines(ax + 16, ay + 416, ["- Behaviour for every kind lives in one big switch", "- Users cannot define their own kind", "- Validation rules are scattered"], 12, 17)

    # panel B
    bx, by, bw, bh = 500, 76, 456, 492
    s.rect(bx, by, bw, bh, GREY_BG)
    s.text(bx + 16, by + 28, "Option B: base class + a subclass per kind", 15, 700, GREEN)
    s.text(bx + 16, by + 48, "behaviour lives ON the classes (retry, resume, stop, validate)", 12, 400, MUTED)
    # base
    s.rect(bx + 118, by + 62, 220, 70, WHITE, "#6b7280")
    s.text(bx + 228, by + 82, "Stage", 14, 700, INK, "middle")
    s.text(bx + 228, by + 100, "undeclared = today's behaviour", 11, 400, MUTED, "middle")
    s.text(bx + 228, by + 117, "no retries or checkpoint allowed", 11, 400, RED, "middle")
    # children
    kids = [("Pure", GREEN, GREEN_BG, ["retry: yes", "re-run on resume: yes", "dropped at a stop: yes"]),
            ("Target", AMBER, AMBER_BG, ["retry: only if idempotent", "marker: .done", "finishes at a stop"]),
            ("Destructive", RED, RED_BG, ["retry: never", "markers: .start .done", "needs recover= or done="])]
    cx = [bx + 12, bx + 160, bx + 308]
    for (name, col, bg, items), x in zip(kids, cx):
        s.line(bx + 228, by + 132, x + 68, by + 162)
        s.rect(x, by + 162, 136, 112, bg, col)
        s.text(x + 68, by + 183, name, 14, 700, col, "middle")
        s.lines(x + 8, by + 205, items, 10.5, 17, INK)
    # scratch under pure
    s.line(cx[0] + 68, by + 274, cx[0] + 68, by + 296)
    s.rect(cx[0], by + 296, 136, 60, GREEN_BG, GREEN)
    s.text(cx[0] + 68, by + 316, "Scratch", 14, 700, GREEN, "middle")
    s.lines(cx[0] + 8, by + 334, ["+ per-item scratch path", "+ cleaned up for you"], 10.5, 15)
    s.text(bx + 16, by + 384, "Good", 13, 700, GREEN)
    s.lines(bx + 16, by + 402, ["+ Each kind owns and tests its own rules", "+ Users can subclass for their own kind", "+ Plain Stage keeps working unchanged"], 12, 16)
    s.text(bx + 250, by + 384, "Costly", 13, 700, RED)
    s.lines(bx + 250, by + 402, ["- Four more public names", "- Kind is fixed by inheritance"], 12, 16)
    s.text(bx + 16, by + 474, "Leaning: Option B. The kinds differ in behaviour, not in data, and plain Stage stays safe.", 11.5, 600, GREEN)
    s.save("stage-hierarchy.svg", 590)


# ---------------------------------------------------------------- 2. effect matrix
def matrix():
    s = Svg(980, "What the library may do, by kind of stage", "A grid of stage kinds against retry, re-run on resume, skip on resume, behaviour at a stop, and what the user must provide.")
    s.text(24, 34, "What the library may do automatically, by kind of stage", 17, 700)
    s.text(24, 56, "Proposed. Green: yes. Amber: only under a condition. Red: never (the user must handle it).", 12, 400, MUTED)
    cols = [("Kind of stage", 190), ("Retry in a run", 130), ("Re-run on resume", 150), ("Skip on resume", 150), ("At a graceful stop", 170), ("You must provide", 134)]
    x0, y0 = 24, 76
    x = x0
    for name, w in cols:
        s.rect(x, y0, w, 34, "#e5e7eb", LINE, 4)
        s.text(x + 8, y0 + 22, name, 12, 700)
        x += w
    G, A, R, N = (GREEN_BG, GREEN), (AMBER_BG, AMBER), (RED_BG, RED), (GREY_BG, MUTED)
    rows = [
        ("Stage (undeclared)", "today's behaviour", [("not allowed", R), ("not allowed", R), ("not allowed", R), ("finish in flight", N), ("declare a kind", N)]),
        ("Pure (reads, passes on)", "e.g. read a file", [("yes", G), ("yes", G), ("if output saved", A), ("dropped, recomputed", G), ("nothing", N)]),
        ("Scratch (working files)", "private, regenerable", [("yes, overwrite", G), ("yes", G), ("if output saved", A), ("dropped, recomputed", G), ("nothing (auto cleanup)", N)]),
        ("Target, idempotent=True", "e.g. upsert, atomic write", [("yes", G), ("yes", G), ("if output saved", A), ("dropped", G), ("assert idempotent", N)]),
        ("Target (not idempotent)", "visible output", [("no", R), ("after a marker check", A), ("if .done marker", A), ("finishes the call", A), ("recover= optional", N)]),
        ("Destructive", "delete, move, send, bill", [("never", R), ("only via recover=", A), ("if .done or recover says so", A), ("finishes the call", A), ("recover= (required)", N)]),
    ]
    y = y0 + 40
    for kind, sub, cells in rows:
        s.rect(x0, y, 190, 50, WHITE, LINE, 4)
        s.text(x0 + 8, y + 21, kind, 12.5, 700)
        s.text(x0 + 8, y + 38, sub, 11, 400, MUTED)
        x = x0 + 190
        for (label, (bg, fg)), (name, w) in zip(cells, cols[1:]):
            s.rect(x, y, w, 50, bg, LINE, 4)
            s.text(x + w / 2, y + 30, label, 11.5, 600, fg, "middle")
            x += w
        y += 56
    s.text(24, y + 14, "Retry is a separate opt-in (retries=N), off by default. Skipping a stage needs its output saved, or recover= must supply a value.", 12, 400, MUTED)
    s.text(24, y + 34, "A destructive stage must be first or follow a saved stage (its input must still exist on a restart). It is allowed only with custom recovery logic.", 12, 400, MUTED)
    s.save("effect-matrix.svg", y + 52)


# ---------------------------------------------------------------- 3. stop modes
def stop_modes():
    s = Svg(980, "What happens to in-flight items at a graceful stop", "Five example items at the moment a stop is requested, and what each stop mode does with them.")
    s.text(24, 34, "Decision 3: what happens to items already in the pipeline when a stop is requested", 17, 700)
    s.text(24, 56, "Example pipeline: read (Pure, not saved) -> upload (Target, an effect, not saved) -> save (Pure, saved). A stop arrives at this moment:", 12, 400, MUTED)
    cols = [("Item, at the moment of the stop", 260), ("No drain  (drain=False)", 220), ("Automatic  (drain=None)", 240), ("Drain everything  (drain=True)", 224)]
    x0, y0 = 24, 76
    x = x0
    for name, w in cols:
        s.rect(x, y0, w, 34, "#e5e7eb", LINE, 4)
        s.text(x + 8, y0 + 22, name, 12, 700)
        x += w
    G, A, R, N = (GREEN_BG, GREEN), (AMBER_BG, AMBER), (RED_BG, RED), (GREY_BG, MUTED)
    D = ("dropped, redone later", R)
    rows = [
        ("1  waiting for read", "nothing started, no effect yet", [D, D, ("runs to the end", G)]),
        ("2  read is running", "no effect yet", [("call ends, then dropped", R), ("call ends, then dropped", R), ("runs to the end", G)]),
        ("3  waiting for upload", "read done, value only in memory", [D, D, ("uploads, then saves", G)]),
        ("4  upload is running", "the effect is happening", [("call ends, marker written, dropped", A), ("call ends, goes on to save", G), ("call ends, goes on to save", G)]),
        ("5  waiting for save", "upload done, result only in memory", [("dropped; recover= decides on restart", A), ("goes on to save", G), ("goes on to save", G)]),
    ]
    y = y0 + 40
    widths = [c[1] for c in cols]
    for item, sub, cells in rows:
        s.rect(x0, y, widths[0], 56, WHITE, LINE, 4)
        s.text(x0 + 8, y + 23, item, 12.5, 700)
        s.text(x0 + 8, y + 41, sub, 11, 400, MUTED)
        x = x0 + widths[0]
        for (label, (bg, fg)), w in zip(cells, widths[1:]):
            s.rect(x, y, w, 56, bg, LINE, 4)
            words, line1, line2 = label.split(), "", ""
            for wd in words:
                if len(line1) + len(wd) < 27 and not line2:
                    line1 += (" " if line1 else "") + wd
                else:
                    line2 += (" " if line2 else "") + wd
            if line2:
                s.text(x + w / 2, y + 24, line1, 11.5, 600, fg, "middle")
                s.text(x + w / 2, y + 41, line2, 11.5, 600, fg, "middle")
            else:
                s.text(x + w / 2, y + 33, line1, 11.5, 600, fg, "middle")
            x += w
        y += 62
    s.text(24, y + 14, "Key: green = keeps going.  Red = dropped (not an error: nothing is written, the item is redone on the next run).  Amber = depends on a marker or recover=.", 12, 600, INK)
    y += 22
    s.text(24, y + 14, "Automatic rule: if an effect already happened and its result exists only in memory, the item keeps going until its data is saved.", 12, 600, MUTED)
    s.text(24, y + 34, "Otherwise it is dropped at the next stage boundary. A call that is already running always finishes. Dropped items write nothing.", 12, 400, MUTED)
    s.text(24, y + 54, "Drain everything also starts the upload for items 1 to 3: that is what you ask for with drain=True. Automatic never starts a new effect.", 12, 400, MUTED)
    s.save("stop-modes.svg", y + 74)


hierarchy()
matrix()
stop_modes()
