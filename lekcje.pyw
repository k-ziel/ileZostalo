import datetime
import importlib.util
import json
import os
import queue
import sys
import tkinter as tk
import tkinter.font as tkfont
from tkinter import colorchooser

# Ikona w zasobniku systemowym (opcjonalna): pip install pystray pillow
try:
    import pystray
    from PIL import Image, ImageDraw

    TRAY_AVAILABLE = True
except ImportError:
    TRAY_AVAILABLE = False

IS_WIN = sys.platform == "win32"

# Brakujące pakiety (nazwa pip -> nazwa pakietu apt na Debianie/Ubuntu)
APT_NAMES = {"pystray": "python3-pystray", "pillow": "python3-pil",
             "python-xlib": "python3-xlib"}
MISSING_PACKAGES = []
if importlib.util.find_spec("pystray") is None:
    MISSING_PACKAGES.append("pystray")
if importlib.util.find_spec("PIL") is None:
    MISSING_PACKAGES.append("pillow")
if not IS_WIN and importlib.util.find_spec("Xlib") is None:
    MISSING_PACKAGES.append("python-xlib")

LINUX_ALPHA = 0.55  # Linux: "przezroczyste tło" = półprzezroczyste całe okno


def x11_shape_available():
    """Linux: click-through działa przez rozszerzenie XShape (python-xlib)."""
    try:
        from Xlib import display
        from Xlib.ext import shape  # noqa: F401

        d = display.Display()
        try:
            return bool(d.has_extension("SHAPE"))
        finally:
            d.close()
    except Exception:
        return False


# Click-through wymaga ikony w zasobniku (żeby dało się go wyłączyć)
CLICK_THROUGH_AVAILABLE = TRAY_AVAILABLE and (IS_WIN or x11_shape_available())

# ---------------------------------------------------------------------------
# Konfiguracja
#   Windows: %LOCALAPPDATA%/.serek/config.json
#   Linux:   $XDG_CONFIG_HOME/.serek/config.json (domyślnie ~/.config/.serek)
# ---------------------------------------------------------------------------
if IS_WIN:
    _CONFIG_BASE = os.environ.get("LOCALAPPDATA", os.path.expanduser("~"))
else:
    _CONFIG_BASE = os.environ.get("XDG_CONFIG_HOME",
                                  os.path.expanduser("~/.config"))
CONFIG_DIR = os.path.join(_CONFIG_BASE, ".serek")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")

DEFAULT_CONFIG = {
    "bg_color": "#1e1e1e",
    "text_color": "#00ffcc",
    "font_size": 11,
    "pos_x": None,
    "pos_y": None,
    "transparent_bg": False,
    "shadow": True,
    "outline_color": "#000000",
    "outline_alpha": 100,  # 0-100 %
    "warn_minutes": 5,  # poniżej tylu minut do końca lekcji zmiana koloru (0 = wył.)
    "warn_color": "#ff3b30",
    "show_end_label": True,  # napis "Do końca:" przed czasem
    "click_through": False,
    "hide_missing_dialog": False,
}

MIN_SIZE, MAX_SIZE = 8, 40
KEY_COLOR = "#010101"  # kolor "dziury" w oknie przy przezroczystym tle


def load_config():
    config = dict(DEFAULT_CONFIG)
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        for key in ("bg_color", "text_color"):
            if isinstance(data.get(key), str):
                config[key] = data[key]
        size = data.get("font_size")
        if isinstance(size, int) and MIN_SIZE <= size <= MAX_SIZE:
            config["font_size"] = size
        for key in ("pos_x", "pos_y"):
            if isinstance(data.get(key), int):
                config[key] = data[key]
        for key in ("transparent_bg", "shadow", "click_through",
                    "hide_missing_dialog", "show_end_label"):
            if isinstance(data.get(key), bool):
                config[key] = data[key]
        if isinstance(data.get("outline_color"), str):
            config["outline_color"] = data["outline_color"]
        alpha = data.get("outline_alpha")
        if isinstance(alpha, int) and 0 <= alpha <= 100:
            config["outline_alpha"] = alpha
        if isinstance(data.get("warn_color"), str):
            config["warn_color"] = data["warn_color"]
        warn = data.get("warn_minutes")
        if isinstance(warn, int) and 0 <= warn <= 30:
            config["warn_minutes"] = warn
    except (OSError, ValueError):
        pass
    return config


def save_config():
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)
    except OSError:
        pass


config = load_config()
if config["click_through"] and not CLICK_THROUGH_AVAILABLE:
    # Bez ikony w zasobniku nie dałoby się już wyłączyć click-through
    config["click_through"] = False

# Godziny lekcji z planu zajęć
LESSONS = [
    ("07:10", "07:55", "0"),
    ("08:00", "08:45", "1"),
    ("08:50", "09:35", "2"),
    ("09:40", "10:25", "3"),
    ("10:40", "11:25", "4"),
    ("11:30", "12:15", "5"),
    ("12:30", "13:15", "6"),
    ("13:20", "14:05", "7"),
    ("14:10", "14:55", "8"),
    ("15:00", "15:45", "9"),
    ("15:50", "16:35", "10"),
]


def compute_text():
    """Zwraca (tekst, sekundy do końca lekcji albo None poza lekcją)."""
    now = datetime.datetime.now()

    if now.weekday() >= 5:
        return "Weekend – brak lekcji", None

    for start_str, end_str, num in LESSONS:
        sh, sm = map(int, start_str.split(":"))
        eh, em = map(int, end_str.split(":"))

        start_dt = now.replace(hour=sh, minute=sm, second=0, microsecond=0)
        end_dt = now.replace(hour=eh, minute=em, second=0, microsecond=0)

        if start_dt <= now < end_dt:
            diff = int((end_dt - now).total_seconds())
            m, s = divmod(diff, 60)
            label = "Do końca: " if config["show_end_label"] else ""
            return f"{label}{m:02d}:{s:02d}", diff
        elif now < start_dt:
            diff = int((start_dt - now).total_seconds())
            m, s = divmod(diff, 60)
            return f"Do dzwonka: {m:02d}:{s:02d}", None

    return "Lekcje na dziś zakończone", None


# ---------------------------------------------------------------------------
# Okno główne + rysowanie tekstu (canvas, żeby dało się zrobić cień)
# ---------------------------------------------------------------------------
root = tk.Tk()
root.overrideredirect(True)  # Brak ramki okna
root.attributes("-topmost", True)  # Zawsze na wierzchu
root.withdraw()  # pokażemy po ewentualnym oknie z brakującymi pakietami


def pick_font(candidates, tk_named_font):
    """Pierwsza zainstalowana czcionka z listy, a jak brak – domyślna Tk."""
    available = set(tkfont.families(root))
    for name in candidates:
        if name in available:
            return name
    return tkfont.nametofont(tk_named_font).actual("family")


MONO_FONT = pick_font(["Consolas", "DejaVu Sans Mono", "Liberation Mono",
                       "Noto Sans Mono", "Ubuntu Mono", "Courier New"],
                      "TkFixedFont")
UI_FONT = pick_font(["Segoe UI", "DejaVu Sans", "Liberation Sans",
                     "Noto Sans", "Ubuntu"], "TkDefaultFont")

text_font = tkfont.Font(root=root, family=MONO_FONT, size=config["font_size"],
                        weight="bold")
canvas = tk.Canvas(root, highlightthickness=0, borderwidth=0, cursor="fleur")
canvas.pack()

current_text = "Ładowanie..."
current_remaining = None  # sekundy do końca lekcji (None = poza lekcją)


def refresh_text():
    global current_text, current_remaining
    current_text, current_remaining = compute_text()


def is_warning():
    return (current_remaining is not None and config["warn_minutes"] > 0
            and current_remaining < config["warn_minutes"] * 60)


def current_text_color():
    return config["warn_color"] if is_warning() else config["text_color"]


def outline_base_color():
    """Kolor obrysu przed uwzględnieniem krycia (stały, ustawiany w opcjach)."""
    return config["outline_color"]


def blend(fg, bg, alpha):
    r1, g1, b1 = root.winfo_rgb(fg)
    r2, g2, b2 = root.winfo_rgb(bg)

    def mix(c1, c2):
        return int((c1 * alpha + c2 * (1 - alpha)) / 257)

    return f"#{mix(r1, r2):02x}{mix(g1, g2):02x}{mix(b1, b2):02x}"


def outline_color_with_alpha():
    """Tk nie ma alfy per element, więc krycie to mieszanie koloru obrysu
    z kolorem tła (bg_color)."""
    color = blend(outline_base_color(), config["bg_color"],
                  config["outline_alpha"] / 100)
    return "#020202" if color == KEY_COLOR else color  # nie myl z kluczem


def render():
    canvas.delete("all")
    text_font.configure(size=config["font_size"])
    tw = text_font.measure(current_text)
    th = text_font.metrics("linespace")
    off = max(1, round(config["font_size"] / 8))
    pad_x, pad_y = 12, 8
    canvas.config(width=tw + 2 * pad_x + off + 2, height=th + 2 * pad_y + off + 2)

    x, y = pad_x + 1, pad_y + 1
    if (config["transparent_bg"] and config["shadow"]
            and config["outline_alpha"] > 0):
        sc = outline_color_with_alpha()
        # cienka obwódka + przesunięty cień
        for dx, dy in ((-1, -1), (0, -1), (1, -1), (-1, 0), (1, 0),
                       (-1, 1), (0, 1), (1, 1), (off, off)):
            canvas.create_text(x + dx, y + dy, text=current_text, font=text_font,
                               fill=sc, anchor="nw")
    canvas.create_text(x, y, text=current_text, font=text_font,
                       fill=current_text_color(), anchor="nw")


def apply_style():
    transparent = config["transparent_bg"]
    if IS_WIN:
        # Windows: kolor-klucz staje się "dziurą" w oknie
        bg = KEY_COLOR if transparent else config["bg_color"]
        try:
            root.attributes("-transparentcolor", KEY_COLOR if transparent else "")
        except tk.TclError:
            pass
    else:
        # Linux: Tk nie ma przezroczystości per piksel, więc półprzezroczyste
        # całe okno (wymaga kompozytora, np. w GNOME/KDE/Xfce z compositingiem)
        bg = config["bg_color"]
        try:
            root.attributes("-alpha", LINUX_ALPHA if transparent else 1.0)
        except tk.TclError:
            pass
    root.configure(bg=bg)
    canvas.configure(bg=bg)
    refresh_text()
    render()
    refresh_tooltip()


def update_timer():
    global current_text, current_remaining
    state = compute_text()
    if state != (current_text, current_remaining):
        current_text, current_remaining = state
        render()
    if not IS_WIN:
        # Okna override-redirect na X11 ignorują "-topmost", więc podnosimy je
        root.lift()
    root.after(1000, update_timer)


# ---------------------------------------------------------------------------
# Click-through (Windows)
# ---------------------------------------------------------------------------
def _set_click_through_x11(enabled):
    try:
        from Xlib import X, display
        from Xlib.ext import shape

        root.update_idletasks()
        ids = {root.winfo_id()}
        try:
            ids.add(int(root.wm_frame(), 16))  # okno-opakowanie Tk
        except (tk.TclError, ValueError):
            pass

        d = display.Display()
        try:
            for wid in ids:
                win = d.create_resource_object("window", wid)
                if enabled:
                    # pusty region wejścia = kliknięcia przechodzą przez okno
                    win.shape_rectangles(shape.SO.Set, shape.SK.Input,
                                         0, 0, 0, [])
                else:
                    win.shape_mask(shape.SO.Set, shape.SK.Input, 0, 0, X.NONE)
            d.sync()
        finally:
            d.close()
    except Exception as e:
        print("Click-through (X11) nie zadziałał:", e)


def set_click_through(enabled):
    if not IS_WIN:
        _set_click_through_x11(enabled)
        return
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetAncestor.restype = wintypes.HWND
    user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetWindowLongW.restype = ctypes.c_long
    user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
    user32.SetWindowLongW.restype = ctypes.c_long

    GWL_EXSTYLE = -20
    WS_EX_LAYERED = 0x00080000
    WS_EX_TRANSPARENT = 0x00000020

    root.update_idletasks()
    hwnd = user32.GetAncestor(root.winfo_id(), 2) or root.winfo_id()  # GA_ROOT
    style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    if enabled:
        style |= WS_EX_LAYERED | WS_EX_TRANSPARENT
    else:
        style &= ~WS_EX_TRANSPARENT
    user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)


# ---------------------------------------------------------------------------
# Akcje wspólne dla tooltipa i ikony w zasobniku
# ---------------------------------------------------------------------------
dialog_open = False
tray_icon = None
ui_queue = queue.Queue()  # wywołania z wątku zasobnika trafiają tutaj


def run_ui(fn):
    ui_queue.put(fn)


def poll_queue():
    try:
        while True:
            ui_queue.get_nowait()()
    except queue.Empty:
        pass
    except Exception as e:  # nie zabijaj pętli przez błąd w akcji
        print("Błąd akcji UI:", e)
    root.after(100, poll_queue)


def pick_color(key):
    global dialog_open
    dialog_open = True
    try:
        result = colorchooser.askcolor(
            color=config[key], parent=root, title="Wybierz kolor"
        )
    finally:
        dialog_open = False
    if result and result[1]:
        config[key] = result[1]
        save_config()
        apply_style()
    schedule_hide()


def change_size(delta):
    new = max(MIN_SIZE, min(MAX_SIZE, config["font_size"] + delta))
    if new == config["font_size"]:
        return
    config["font_size"] = new
    save_config()
    apply_style()
    root.update_idletasks()
    place_tooltip()


def change_outline_alpha(delta):
    new = max(0, min(100, config["outline_alpha"] + delta))
    if new == config["outline_alpha"]:
        return
    config["outline_alpha"] = new
    save_config()
    apply_style()


def change_warn_minutes(delta):
    new = max(0, min(30, config["warn_minutes"] + delta))
    if new == config["warn_minutes"]:
        return
    config["warn_minutes"] = new
    save_config()
    apply_style()


def toggle(key):
    config[key] = not config[key]
    save_config()
    if key == "click_through":
        tooltip.withdraw()
        set_click_through(config[key])
    apply_style()


def quit_app(event=None):
    """Zapisuje ostatnią pozycję okna i zamyka program."""
    config["pos_x"] = root.winfo_x()
    config["pos_y"] = root.winfo_y()
    save_config()
    if tray_icon is not None:
        try:
            tray_icon.stop()
        except Exception:
            pass
    root.destroy()


# ---------------------------------------------------------------------------
# Tooltip z opcjami (po najechaniu myszką)
# ---------------------------------------------------------------------------
TIP_BG = "#2b2b2b"
TIP_FG = "#e6e6e6"

tooltip = tk.Toplevel(root)
tooltip.withdraw()
tooltip.overrideredirect(True)
tooltip.attributes("-topmost", True)
tooltip.configure(bg=TIP_BG, highlightthickness=1, highlightbackground="#555555")

hide_job = None


def make_color_row(text, key):
    row = tk.Frame(tooltip, bg=TIP_BG, cursor="hand2")
    row.pack(fill="x", padx=8, pady=3)
    swatch = tk.Frame(row, width=16, height=16, bg=config[key],
                      highlightthickness=1, highlightbackground="#888888")
    swatch.pack(side="left")
    txt = tk.Label(row, text=text, bg=TIP_BG, fg=TIP_FG,
                   font=(UI_FONT, 9), cursor="hand2")
    txt.pack(side="left", padx=(8, 0))
    for w in (row, swatch, txt):
        w.bind("<Button-1>", lambda e, k=key: pick_color(k))
    return swatch


toggle_labels = {}


def make_toggle_row(text, key):
    lbl = tk.Label(tooltip, text="", bg=TIP_BG, fg=TIP_FG, anchor="w",
                   font=(UI_FONT, 9), cursor="hand2")
    lbl.pack(fill="x", padx=8, pady=3)
    lbl.bind("<Button-1>", lambda e, k=key: toggle(k))
    toggle_labels[key] = (lbl, text)


tk.Label(tooltip, text="Opcje", bg=TIP_BG, fg="#888888",
         font=(UI_FONT, 8, "bold")).pack(anchor="w", padx=8, pady=(6, 0))
bg_swatch = make_color_row("Kolor tła", "bg_color")
text_swatch = make_color_row("Kolor tekstu", "text_color")

size_row = tk.Frame(tooltip, bg=TIP_BG)
size_row.pack(fill="x", padx=8, pady=3)
tk.Label(size_row, text="Rozmiar", bg=TIP_BG, fg=TIP_FG,
         font=(UI_FONT, 9)).pack(side="left")
plus_btn = tk.Label(size_row, text=" + ", bg="#3a3a3a", fg=TIP_FG,
                    font=(UI_FONT, 9, "bold"), cursor="hand2")
plus_btn.pack(side="right")
size_value = tk.Label(size_row, text=str(config["font_size"]), bg=TIP_BG,
                      fg=TIP_FG, font=(UI_FONT, 9), width=3)
size_value.pack(side="right")
minus_btn = tk.Label(size_row, text=" − ", bg="#3a3a3a", fg=TIP_FG,
                     font=(UI_FONT, 9, "bold"), cursor="hand2")
minus_btn.pack(side="right", padx=(12, 0))
plus_btn.bind("<Button-1>", lambda e: change_size(1))
minus_btn.bind("<Button-1>", lambda e: change_size(-1))

make_toggle_row("Przezroczyste tło" if IS_WIN else "Półprzezroczyste okno",
                "transparent_bg")
make_toggle_row("Cień tekstu (przy przezr. tle)", "shadow")
outline_swatch = make_color_row("Kolor obrysu", "outline_color")

alpha_row = tk.Frame(tooltip, bg=TIP_BG)
alpha_row.pack(fill="x", padx=8, pady=3)
tk.Label(alpha_row, text="Krycie obrysu", bg=TIP_BG, fg=TIP_FG,
         font=(UI_FONT, 9)).pack(side="left")
alpha_plus = tk.Label(alpha_row, text=" + ", bg="#3a3a3a", fg=TIP_FG,
                      font=(UI_FONT, 9, "bold"), cursor="hand2")
alpha_plus.pack(side="right")
outline_alpha_value = tk.Label(alpha_row, text="", bg=TIP_BG, fg=TIP_FG,
                               font=(UI_FONT, 9), width=5)
outline_alpha_value.pack(side="right")
alpha_minus = tk.Label(alpha_row, text=" − ", bg="#3a3a3a", fg=TIP_FG,
                       font=(UI_FONT, 9, "bold"), cursor="hand2")
alpha_minus.pack(side="right", padx=(12, 0))
alpha_plus.bind("<Button-1>", lambda e: change_outline_alpha(10))
alpha_minus.bind("<Button-1>", lambda e: change_outline_alpha(-10))

warn_swatch = make_color_row("Kolor ostrzeżenia", "warn_color")

warn_row = tk.Frame(tooltip, bg=TIP_BG)
warn_row.pack(fill="x", padx=8, pady=3)
tk.Label(warn_row, text="Ostrzeżenie poniżej", bg=TIP_BG, fg=TIP_FG,
         font=(UI_FONT, 9)).pack(side="left")
warn_plus = tk.Label(warn_row, text=" + ", bg="#3a3a3a", fg=TIP_FG,
                     font=(UI_FONT, 9, "bold"), cursor="hand2")
warn_plus.pack(side="right")
warn_value = tk.Label(warn_row, text="", bg=TIP_BG, fg=TIP_FG,
                      font=(UI_FONT, 9), width=6)
warn_value.pack(side="right")
warn_minus = tk.Label(warn_row, text=" − ", bg="#3a3a3a", fg=TIP_FG,
                      font=(UI_FONT, 9, "bold"), cursor="hand2")
warn_minus.pack(side="right", padx=(12, 0))
warn_plus.bind("<Button-1>", lambda e: change_warn_minutes(1))
warn_minus.bind("<Button-1>", lambda e: change_warn_minutes(-1))

make_toggle_row("Napis „Do końca:”", "show_end_label")
if CLICK_THROUGH_AVAILABLE:
    make_toggle_row("Click-through (opcje w zasobniku)", "click_through")

close_btn = tk.Label(tooltip, text="Zamknij program", bg=TIP_BG, fg="#ff6b6b",
                     font=(UI_FONT, 9), cursor="hand2")
close_btn.pack(anchor="w", padx=8, pady=(3, 6))
close_btn.bind("<Button-1>", quit_app)


def refresh_tooltip():
    bg_swatch.configure(bg=config["bg_color"])
    text_swatch.configure(bg=config["text_color"])
    size_value.config(text=str(config["font_size"]))
    outline_swatch.configure(bg=outline_base_color())
    outline_alpha_value.config(text=f'{config["outline_alpha"]}%')
    warn_swatch.configure(bg=config["warn_color"])
    warn_value.config(text=f'{config["warn_minutes"]} min'
                      if config["warn_minutes"] else "wył.")
    for key, (lbl, text) in toggle_labels.items():
        lbl.config(text=("☑ " if config[key] else "☐ ") + text)


def place_tooltip():
    tooltip.update_idletasks()
    tw = tooltip.winfo_reqwidth()
    th = tooltip.winfo_reqheight()
    x = root.winfo_x()
    y = root.winfo_y() - th - 4  # nad zegarem
    if y < 0:
        y = root.winfo_y() + root.winfo_height() + 4  # pod zegarem
    x = max(0, min(x, root.winfo_screenwidth() - tw))
    tooltip.geometry(f"+{x}+{y}")


def show_tooltip(event=None):
    global hide_job
    if hide_job:
        root.after_cancel(hide_job)
        hide_job = None
    if tooltip.winfo_viewable():
        return
    place_tooltip()
    tooltip.deiconify()
    tooltip.lift()


def show_tooltip_at_pointer():
    """Pokazuje to samo okno opcji przy kursorze (wywołanie z ikony w zasobniku)."""
    global hide_job
    cancel_hide()
    px, py = root.winfo_pointerxy()
    tooltip.update_idletasks()
    tw = tooltip.winfo_reqwidth()
    th = tooltip.winfo_reqheight()
    sw = root.winfo_screenwidth()
    sh = root.winfo_screenheight()
    x = max(0, min(px - tw // 2, sw - tw))
    y = py - th - 12 if py > sh // 2 else py + 12
    y = max(0, min(y, sh - th))
    tooltip.geometry(f"+{x}+{y}")
    tooltip.deiconify()
    tooltip.attributes("-topmost", True)
    tooltip.lift()
    # jeśli kursor nie wejdzie na okno, schowaj je po chwili
    hide_job = root.after(6000, hide_tooltip)


def hide_tooltip():
    global hide_job
    hide_job = None
    if dialog_open:
        return
    tooltip.withdraw()


def schedule_hide(event=None):
    global hide_job
    if hide_job:
        root.after_cancel(hide_job)
    hide_job = root.after(300, hide_tooltip)


def cancel_hide(event=None):
    global hide_job
    if hide_job:
        root.after_cancel(hide_job)
        hide_job = None


canvas.bind("<Enter>", show_tooltip)
canvas.bind("<Leave>", schedule_hide)
tooltip.bind("<Enter>", cancel_hide)
tooltip.bind("<Leave>", schedule_hide)


# ---------------------------------------------------------------------------
# Przesuwanie okna myszką
# ---------------------------------------------------------------------------
def start_move(event):
    root.x = event.x
    root.y = event.y
    tooltip.withdraw()


def do_move(event):
    x = root.winfo_x() + (event.x - root.x)
    y = root.winfo_y() + (event.y - root.y)
    root.geometry(f"+{x}+{y}")


canvas.bind("<Button-1>", start_move)
canvas.bind("<B1-Motion>", do_move)

# Prawy przycisk myszy zamyka program
canvas.bind("<Button-3>", quit_app)
root.protocol("WM_DELETE_WINDOW", quit_app)


# ---------------------------------------------------------------------------
# Ikona w zasobniku systemowym (menu z opcjami, działa też przy click-through)
# ---------------------------------------------------------------------------
def make_tray_image():
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    white = (255, 255, 255, 255)
    d.ellipse((4, 4, 60, 60), outline=white, width=5)
    d.line((32, 32, 32, 14), fill=white, width=5)
    d.line((32, 32, 45, 39), fill=white, width=5)
    return img


def start_tray():
    global tray_icon, TRAY_AVAILABLE
    item = pystray.MenuItem
    menu = pystray.Menu(
        item("Opcje…", lambda i, it: run_ui(show_tooltip_at_pointer)),
        pystray.Menu.SEPARATOR,
        item("Kolor tła…", lambda i, it: run_ui(lambda: pick_color("bg_color"))),
        item("Kolor tekstu…", lambda i, it: run_ui(lambda: pick_color("text_color"))),
        item("Kolor obrysu…", lambda i, it: run_ui(lambda: pick_color("outline_color"))),
        item("Krycie obrysu +", lambda i, it: run_ui(lambda: change_outline_alpha(10))),
        item("Krycie obrysu −", lambda i, it: run_ui(lambda: change_outline_alpha(-10))),
        item("Kolor ostrzeżenia…", lambda i, it: run_ui(lambda: pick_color("warn_color"))),
        item("Ostrzeżenie: więcej minut", lambda i, it: run_ui(lambda: change_warn_minutes(1))),
        item("Ostrzeżenie: mniej minut", lambda i, it: run_ui(lambda: change_warn_minutes(-1))),
        item("Rozmiar +", lambda i, it: run_ui(lambda: change_size(1))),
        item("Rozmiar −", lambda i, it: run_ui(lambda: change_size(-1))),
        pystray.Menu.SEPARATOR,
        item("Przezroczyste tło" if IS_WIN else "Półprzezroczyste okno", lambda i, it: run_ui(lambda: toggle("transparent_bg")),
             checked=lambda it: config["transparent_bg"]),
        item("Cień tekstu", lambda i, it: run_ui(lambda: toggle("shadow")),
             checked=lambda it: config["shadow"]),
        item("Napis „Do końca:”", lambda i, it: run_ui(lambda: toggle("show_end_label")),
             checked=lambda it: config["show_end_label"]),
        # domyślna akcja = lewy klik na ikonie
        item("Click-through", lambda i, it: run_ui(lambda: toggle("click_through")),
             checked=lambda it: config["click_through"], default=True,
             visible=lambda it: CLICK_THROUGH_AVAILABLE),
        pystray.Menu.SEPARATOR,
        item("Zamknij", lambda i, it: run_ui(quit_app)),
    )
    try:
        tray_icon = pystray.Icon("serek", make_tray_image(),
                                 "Serek – plan lekcji", menu)
        if IS_WIN:
            try:
                from pystray._util import win32 as _w32

                original = tray_icon._message_handlers[_w32.WM_NOTIFY]

                def on_notify(wparam, lparam):
                    if lparam == _w32.WM_RBUTTONUP:
                        run_ui(show_tooltip_at_pointer)
                        return 0
                    return original(wparam, lparam)

                tray_icon._message_handlers[_w32.WM_NOTIFY] = on_notify
            except Exception as e:
                print("Prawy klik w zasobniku: używam natywnego menu:", e)
        tray_icon.run_detached()
    except Exception as e:
        print("Nie udało się uruchomić ikony w zasobniku:", e)
        tray_icon = None


if TRAY_AVAILABLE:
    start_tray()
else:
    print("Brak pystray/pillow – ikona w zasobniku wyłączona "
          "(pip install pystray pillow).")
if TRAY_AVAILABLE and not IS_WIN and not CLICK_THROUGH_AVAILABLE:
    print("Click-through wyłączony – potrzebny X11 i python-xlib "
          "(pip install python-xlib).")


# ---------------------------------------------------------------------------
# Okno z komendami instalacji brakujących pakietów
# ---------------------------------------------------------------------------
def show_missing_packages_dialog():
    """Pokazuje komendy do skopiowania, jeśli brakuje pakietów (Windows/Linux)."""
    if not MISSING_PACKAGES or config["hide_missing_dialog"]:
        return

    pkgs = " ".join(MISSING_PACKAGES)
    if IS_WIN:
        commands = [
            ("Wklej w CMD lub PowerShellu:", f"python -m pip install {pkgs}"),
            ("Jeśli powyższe nie działa:", f"py -m pip install {pkgs}"),
        ]
    else:
        apt = " ".join(APT_NAMES[p] for p in MISSING_PACKAGES)
        commands = [
            ("Wklej w terminalu:", f"python3 -m pip install {pkgs}"),
            ("Debian/Ubuntu – gdy pip zgłasza „externally-managed-environment”:",
             f"sudo apt install {apt}"),
        ]

    dlg = tk.Toplevel(root)
    dlg.title("Serek – brakujące pakiety")
    dlg.configure(bg=TIP_BG, padx=16, pady=14)
    dlg.attributes("-topmost", True)
    dlg.resizable(False, False)

    tk.Label(dlg, text="Brakuje pakietów: " + ", ".join(MISSING_PACKAGES),
             bg=TIP_BG, fg=TIP_FG, font=(UI_FONT, 11, "bold")).pack(anchor="w")
    tk.Label(dlg, justify="left", bg=TIP_BG, fg="#aaaaaa", font=(UI_FONT, 9),
             text="Program działa i bez nich, ale z ograniczeniami (ikona w "
                  "zasobniku, click-through).\nSkopiuj komendę, wklej ją w "
                  "terminalu i uruchom program ponownie.",
             ).pack(anchor="w", pady=(4, 12))

    def copy(cmd, button):
        root.clipboard_clear()
        root.clipboard_append(cmd)
        root.update()
        button.config(text="Skopiowano ✓")

    for caption, cmd in commands:
        tk.Label(dlg, text=caption, bg=TIP_BG, fg=TIP_FG,
                 font=(UI_FONT, 9)).pack(anchor="w")
        row = tk.Frame(dlg, bg=TIP_BG)
        row.pack(fill="x", pady=(2, 10))
        entry = tk.Entry(row, width=60, font=(MONO_FONT, 10), relief="flat",
                         fg="#00ffcc", readonlybackground="#1e1e1e")
        entry.insert(0, cmd)
        entry.configure(state="readonly")
        entry.pack(side="left", fill="x", expand=True, ipady=5)
        btn = tk.Button(row, text="Kopiuj", width=13, font=(UI_FONT, 9))
        btn.configure(command=lambda c=cmd, b=btn: copy(c, b))
        btn.pack(side="left", padx=(8, 0))

    hide_var = tk.BooleanVar(value=False)
    tk.Checkbutton(dlg, text="Nie pokazuj więcej tego okna", variable=hide_var,
                   bg=TIP_BG, fg=TIP_FG, selectcolor="#1e1e1e",
                   activebackground=TIP_BG, activeforeground=TIP_FG,
                   font=(UI_FONT, 9)).pack(anchor="w")

    exit_requested = False

    def finish():
        if hide_var.get():
            config["hide_missing_dialog"] = True
            save_config()
        dlg.destroy()

    def exit_app():
        nonlocal exit_requested
        exit_requested = True
        finish()

    buttons = tk.Frame(dlg, bg=TIP_BG)
    buttons.pack(fill="x", pady=(12, 0))
    tk.Button(buttons, text="Uruchom bez tych pakietów", font=(UI_FONT, 9),
              command=finish).pack(side="left")
    tk.Button(buttons, text="Zamknij program", font=(UI_FONT, 9),
              command=exit_app).pack(side="right")
    dlg.protocol("WM_DELETE_WINDOW", finish)

    # wyśrodkuj na ekranie
    dlg.update_idletasks()
    dx = (dlg.winfo_screenwidth() - dlg.winfo_reqwidth()) // 2
    dy = (dlg.winfo_screenheight() - dlg.winfo_reqheight()) // 3
    dlg.geometry(f"+{dx}+{dy}")

    root.wait_window(dlg)
    if exit_requested:
        root.destroy()
        sys.exit(0)


# ---------------------------------------------------------------------------
# Start
# ---------------------------------------------------------------------------
show_missing_packages_dialog()
apply_style()

# Pozycja startowa: ostatnia zapisana, a jeśli jej brak (lub jest poza
# ekranem) – prawy dolny róg
root.update_idletasks()
w = root.winfo_reqwidth()
h = root.winfo_reqheight()
screen_w = root.winfo_screenwidth()
screen_h = root.winfo_screenheight()
x, y = config["pos_x"], config["pos_y"]
if x is None or y is None or not (0 <= x <= screen_w - 20 and 0 <= y <= screen_h - 20):
    x = screen_w - w - 30
    y = screen_h - h - 70
root.geometry(f"+{x}+{y}")

root.deiconify()
root.update()
if config["click_through"]:
    set_click_through(True)

refresh_text()
render()
poll_queue()
update_timer()
root.mainloop()
