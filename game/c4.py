"""Four in a row on the profile README.

Visitors open an issue titled ``c4|drop|<column>``; the workflow runs
``c4.py move``, the shark answers, and the board images and README are rebuilt.
"""
import argparse
import json
import random
import re
from urllib.parse import quote
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "game" / "state.json"
README = ROOT / "README.md"
IMG_DIR = ROOT / "assets" / "c4"
REPO = "heavy-shark/heavy-shark"

COLS, ROWS = 7, 6
PEOPLE, SHARK = "P", "S"
DEPTH = 5

TITLE_RE = re.compile(r"^c4\|drop\|([1-7])$")
USER_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")


# --- rules -----------------------------------------------------------------

def cell(board, c, r):
    col = board[c]
    return col[r] if r < len(col) else None


def lines():
    for c in range(COLS):
        for r in range(ROWS):
            for dc, dr in ((1, 0), (0, 1), (1, 1), (1, -1)):
                end_c, end_r = c + 3 * dc, r + 3 * dr
                if 0 <= end_c < COLS and 0 <= end_r < ROWS:
                    yield [(c + i * dc, r + i * dr) for i in range(4)]


LINES = list(lines())


def winner(board):
    for line in LINES:
        first = cell(board, *line[0])
        if first and all(cell(board, c, r) == first for c, r in line[1:]):
            return first, line
    return None, None


def full(board):
    return all(len(col) == ROWS for col in board)


def legal(board):
    return [c for c in range(COLS) if len(board[c]) < ROWS]


# --- the shark ---------------------------------------------------------------

def score_line(vals, me, them):
    mine, theirs = vals.count(me), vals.count(them)
    if mine and theirs:
        return 0
    return {0: 0, 1: 1, 2: 6, 3: 40}[mine] - {0: 0, 1: 1, 2: 7, 3: 60}[theirs]


def evaluate(board, me, them):
    total = sum(3 for col in board[3:4] for v in col if v == me)
    for line in LINES:
        total += score_line([cell(board, c, r) for c, r in line], me, them)
    return total


def negamax(board, depth, alpha, beta, me, them):
    w, _ = winner(board)
    if w:
        return (-10_000 - depth) if w == them else (10_000 + depth)
    if full(board):
        return 0
    if depth == 0:
        return evaluate(board, me, them)
    best = -10**9
    for c in sorted(legal(board), key=lambda c: abs(3 - c)):
        board[c].append(me)
        val = -negamax(board, depth - 1, -beta, -alpha, them, me)
        board[c].pop()
        best = max(best, val)
        alpha = max(alpha, val)
        if alpha >= beta:
            break
    return best


def shark_move(board, rng):
    scored = []
    for c in legal(board):
        board[c].append(SHARK)
        scored.append((-negamax(board, DEPTH - 1, -10**9, 10**9, PEOPLE, SHARK), c))
        board[c].pop()
    top = max(s for s, _ in scored)
    return rng.choice([c for s, c in scored if s >= top - 2])


# --- state -----------------------------------------------------------------

def new_state():
    return {
        "board": [[] for _ in range(COLS)],
        "status": "playing",
        "game": 1,
        "score": {"people": 0, "shark": 0, "draw": 0},
        "last": None,
        "players": [],
        "version": 0,
    }


def load():
    return json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else new_state()


def save(state):
    STATE.write_text(json.dumps(state, indent=1) + "\n", encoding="utf-8")


def play(state, col, user, rng):
    """Apply a visitor's move and the shark's reply. Returns the issue comment."""
    if state["status"] != "playing":
        state.update(board=[[] for _ in range(COLS)], status="playing", game=state["game"] + 1, last=None, players=[])
    board = state["board"]
    if len(board[col]) == ROWS:
        return f"Column {col + 1} is full. Pick another one on the [board](https://github.com/heavy-shark)."

    board[col].append(PEOPLE)
    state["last"] = {"user": user, "col": col, "shark": None}
    if user not in state["players"]:
        state["players"] = (state["players"] + [user])[-8:]
    state["version"] += 1

    w, _ = winner(board)
    if w:
        state["status"] = "people"
        state["score"]["people"] += 1
        return f"Column {col + 1}. **Four in a row — you beat the shark!** 🎉\n\n[See the board](https://github.com/heavy-shark)"
    if full(board):
        state["status"] = "draw"
        state["score"]["draw"] += 1
        return f"Column {col + 1}. The board is full: it's a draw.\n\n[See the board](https://github.com/heavy-shark)"

    reply = shark_move(board, rng)
    board[reply].append(SHARK)
    state["last"]["shark"] = reply
    w, _ = winner(board)
    if w:
        state["status"] = "shark"
        state["score"]["shark"] += 1
        return f"You dropped in column {col + 1}. The shark answered in column {reply + 1} — **and that's four in a row for the shark.** 🦈\n\nAny column on the [board](https://github.com/heavy-shark) starts a new game."
    if full(board):
        state["status"] = "draw"
        state["score"]["draw"] += 1
        return f"You dropped in column {col + 1}, the shark answered in {reply + 1}. The board is full: it's a draw.\n\n[See the board](https://github.com/heavy-shark)"
    return f"You dropped in column {col + 1}. The shark answered in column {reply + 1}.\n\n[Your move again](https://github.com/heavy-shark)"


# --- rendering ---------------------------------------------------------------

U = 4                     # one art pixel, in SVG units
CELL = 12                 # art pixels per cell
HEAD = 7                  # art pixels above the board for the drop arrow
W_PX, H_PX = CELL * U, (HEAD + CELL * ROWS + 1) * U

PAL = {
    "board": "#22363F", "edge": "#304A56", "hole": "#0E1A20",
    PEOPLE: ("#F4B740", "#FFE4A3", "#C98A12"),
    SHARK: ("#4FB3A9", "#A5E3DC", "#2D7A73"),
    "arrow": "#F4B740", "ring": "#FFF0D4",
}


def disc_pixels(radius=4.8):
    pts = []
    for y in range(CELL):
        for x in range(CELL):
            if (x - CELL / 2 + .5) ** 2 + (y - CELL / 2 + .5) ** 2 <= radius ** 2:
                pts.append((x, y))
    return pts


DISC = disc_pixels()
HOLE = disc_pixels(5.1)
RING = [p for p in disc_pixels(6.2) if p not in set(HOLE)]


def rects(pts, fill, ox=0, oy=0):
    return "".join(f'<rect x="{(ox + x) * U}" y="{(oy + y) * U}" width="{U}" height="{U}" fill="{fill}"/>' for x, y in pts)


def disc_svg(who, oy):
    base, hi, lo = PAL[who]
    c = CELL / 2 - .5
    return "".join(
        f'<rect x="{x * U}" y="{(oy + y) * U}" width="{U}" height="{U}" '
        f'fill="{hi if (x - c) + (y - c) < -4.5 else lo if (x - c) + (y - c) > 4.5 else base}"/>'
        for x, y in DISC
    )


def column_svg(state, c, win_cells, anim):
    col = state["board"][c]
    playing = state["status"] == "playing"
    top = HEAD
    board_h = (CELL * ROWS + 1) * U
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W_PX}" height="{H_PX}" viewBox="0 0 {W_PX} {H_PX}" shape-rendering="crispEdges">',
        "<style>"
        f"@keyframes drop{{from{{transform:translateY(-{H_PX}px)}}to{{transform:none}}}}"
        ".drop{animation:drop .45s cubic-bezier(.55,0,1,.45) both}"
        ".late{animation-delay:.8s}"
        "@keyframes blink{50%{opacity:0}}.win{animation:blink 1s steps(1) infinite}"
        "@media (prefers-reduced-motion:reduce){.drop,.win{animation:none}}"
        "</style>",
    ]
    if len(col) < ROWS:
        arrow = [(x, y) for y in range(3) for x in range(3 + y, 9 - y)]
        parts.append(f'<g opacity="{1 if playing else .4}">{rects(arrow, PAL["arrow"], 0, 2)}</g>')
    # behind the board: the dark of the holes, then the discs
    parts.append(f'<rect x="0" y="{top * U}" width="{W_PX}" height="{board_h}" fill="{PAL["hole"]}"/>')
    for r, who in enumerate(col):
        oy = top + 1 + (ROWS - 1 - r) * CELL
        cls = anim.get((c, r))
        disc = disc_svg(who, oy)
        parts.append(f'<g class="{cls}">{disc}</g>' if cls else disc)
    # the board face, with the holes cut out
    d = [f"M0 {top * U}h{W_PX}v{board_h}h-{W_PX}z"]
    for r in range(ROWS):
        oy = top + 1 + r * CELL
        d += [f"M{x * U} {(oy + y) * U}h{U}v{U}h-{U}z" for x, y in HOLE]
    parts.append(f'<path fill-rule="evenodd" fill="{PAL["board"]}" d="{"".join(d)}"/>')
    parts.append(f'<rect x="0" y="{top * U}" width="{W_PX}" height="{U}" fill="{PAL["edge"]}"/>')
    for r in range(len(col)):
        if (c, r) in win_cells:
            parts.append(f'<g class="win">{rects(RING, PAL["ring"], 0, top + 1 + (ROWS - 1 - r) * CELL)}</g>')
    parts.append("</svg>")
    return "".join(parts)


def status_lines(state):
    s, last, score = state["status"], state["last"], state["score"]
    if s == "people":
        head = "<b>You beat the shark.</b> Pick any column to start a new game."
    elif s == "shark":
        head = "<b>The shark won this one.</b> Pick any column to start a new game."
    elif s == "draw":
        head = "<b>Draw.</b> Pick any column to start a new game."
    elif not last:
        head = "<b>Your move.</b> Click a column to drop a piece; the shark answers in about a minute."
    else:
        head = "<b>Your move.</b> Click a column; the shark answers in about a minute."
    tail = []
    if last:
        t = f"@{last['user']} dropped in column {last['col'] + 1}"
        if last["shark"] is not None:
            t += f", the shark answered {last['shark'] + 1}"
        tail.append(t)
    tail.append(f"game {state['game']} · people {score['people']} : shark {score['shark']}" + (f" · draws {score['draw']}" if score["draw"] else ""))
    return head, " · ".join(tail)


def issue_link(c):
    body = "Press **Create** to drop your piece. The shark answers in about a minute and the board on the profile updates."
    return f"https://github.com/{REPO}/issues/new?title={quote(f'c4|drop|{c + 1}')}&amp;body={quote(body)}"


def render(state):
    IMG_DIR.mkdir(parents=True, exist_ok=True)
    w, line = winner(state["board"])
    win_cells = set(map(tuple, line)) if line else set()
    anim = {}
    last = state["last"]
    if last:
        board = state["board"]
        if last["shark"] is not None:
            anim[(last["shark"], len(board[last["shark"]]) - 1)] = "drop late"
        pc = last["col"]
        anim[(pc, len(board[pc]) - 1 - (last["shark"] == pc))] = "drop"
    for c in range(COLS):
        (IMG_DIR / f"col{c}.svg").write_text(column_svg(state, c, win_cells, anim), encoding="utf-8")
    v = state["version"]
    cols = "".join(
        f'<a href="{issue_link(c)}"><img src="assets/c4/col{c}.svg?v={v}" width="{W_PX}" alt="Column {c + 1}"></a>'
        for c in range(COLS)
    )
    head, tail = status_lines(state)
    block = (
        "<!-- c4:start -->\n"
        f'<p align="center">{cols}</p>\n\n'
        f'<p align="center">{head}<br><sub>{tail}</sub></p>\n'
        "<!-- c4:end -->"
    )
    text = README.read_text(encoding="utf-8")
    text = re.sub(r"<!-- c4:start -->.*?<!-- c4:end -->", lambda _: block, text, flags=re.S)
    README.write_text(text, encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    mv = sub.add_parser("move")
    mv.add_argument("--title", required=True)
    mv.add_argument("--user", required=True)
    mv.add_argument("--reply", required=True)
    sub.add_parser("render")
    args = ap.parse_args()

    state = load()
    if args.cmd == "move":
        m = TITLE_RE.match(args.title.strip())
        if not m or not USER_RE.match(args.user):
            Path(args.reply).write_text("That doesn't look like a move. Use the column links on the [profile](https://github.com/heavy-shark).", encoding="utf-8")
            return
        msg = play(state, int(m.group(1)) - 1, args.user, random.Random())
        Path(args.reply).write_text(msg, encoding="utf-8")
    save(state)
    render(state)


if __name__ == "__main__":
    main()
