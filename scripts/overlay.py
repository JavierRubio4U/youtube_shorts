# scripts/overlay.py
from pathlib import Path
import logging
from PIL import Image, ImageDraw, ImageFont

# --- Rutas y dirs ---
ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "output" / "state"
STATE.mkdir(parents=True, exist_ok=True)
FONTS_DIR = ROOT / "assets" / "fonts"

# --- Logging ---
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

# --- Parámetros ---
W, H = 1080, 1920
TOP_MARGIN = 420
BOTTOM_MARGIN = 420
LINE_SPACING = 10

def load_fonts():
    # Fuentes estándar del sistema (Windows o Linux)
    system_fonts = [
        ("C:/Windows/Fonts/arial.ttf", 58),  # Arial en Windows
        ("C:/Windows/Fonts/times.ttf", 58),  # Times New Roman en Windows
        ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 58),  # DejaVu Sans en Linux
        ("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf", 58),  # Liberation Sans en Linux
    ]
    
    title_font = None
    small_font = None
    
    try:
        # Intentar cargar fuente para título
        for font_path, size in system_fonts:
            font_file = Path(font_path)
            if font_file.exists():
                title_font = ImageFont.truetype(str(font_file), size)
                logging.info(f"Fuente encontrada para título: {font_path}")
                break
        else:
            logging.warning("No se encontraron fuentes del sistema para título. Usando fuente por defecto.")
            title_font = ImageFont.load_default()
            title_font.size = 48  # Tamaño ajustado para legibilidad
            
        # Intentar cargar fuente para fecha
        for font_path, size in system_fonts:
            font_file = Path(font_path)
            if font_file.exists():
                small_font = ImageFont.truetype(str(font_file), 40)
                logging.info(f"Fuente encontrada para fecha: {font_path}")
                break
        else:
            logging.warning("No se encontraron fuentes del sistema para fecha. Usando fuente por defecto.")
            small_font = ImageFont.load_default()
            small_font.size = 32  # Tamaño ajustado para legibilidad
            
    except Exception as e:
        logging.error(f"Error cargando fuentes: {e}. Usando fuentes por defecto.")
        title_font = ImageFont.load_default()
        title_font.size = 48
        small_font = ImageFont.load_default()
        small_font.size = 32
        
    return title_font, small_font

def wrap_fit(draw, text, font, max_width):
    if not text: return ""
    txt = text.strip()
    while draw.textlength(txt, font=font) > max_width and len(txt) > 4:
        txt = txt[:-1]
    if draw.textlength(txt, font=font) > max_width:
        txt = txt[: max(0, len(txt)-3)] + "..."
    return txt

def wrap_lines(draw, text, font, max_width, max_lines=2, line_spacing=10):
    if not text:
        return [], 0
    words = text.strip().split()
    lines, cur = [], ""
    for w in words:
        trial = (cur + " " + w).strip()
        if draw.textlength(trial, font=font) <= max_width:
            cur = trial
        else:
            if cur: lines.append(cur)
            cur = w
        if len(lines) == max_lines:
            break
    if len(lines) < max_lines and cur:
        lines.append(cur)
    leftover = len(words) > sum(len(l.split()) for l in lines)
    if leftover and lines:
        last = lines[-1]
        while draw.textlength(last + "…", font=font) > max_width and len(last) > 1:
            last = last[:-1]
        lines[-1] = last + "…"
    h = 0
    for i, ln in enumerate(lines):
        _, _, _, bh = draw.textbbox((0,0), ln, font=font)
        h += bh
        if i < len(lines)-1:
            h += line_spacing
    return lines, h

def make_overlay_image(title, fecha, tmdb_id):
    title_f, small_f = load_fonts()
    ov = Image.new("RGBA", (W, H), (0,0,0,0))
    d = ImageDraw.Draw(ov)

    d.rectangle([0, 0, W, TOP_MARGIN], fill=(0,0,0,220))
    d.rectangle([0, H-BOTTOM_MARGIN, W, H], fill=(0,0,0,220))

    def draw_outlined_text(pos, text, font, fill_color, outline_color=(0, 0, 0, 255), outline_width=2):
        x, y = pos
        for dx, dy in [(-outline_width, 0), (outline_width, 0), (0, -outline_width), (0, outline_width)]:
            d.text((x + dx, y + dy), text, font=font, fill=outline_color)
        d.text(pos, text, font=font, fill=fill_color)

    t_lines, t_h = wrap_lines(d, title or "", title_f, int(W*0.94))
    y_title_center = max(10, (TOP_MARGIN - t_h)//2)
    y = y_title_center
    for ln in t_lines:
        lw = d.textlength(ln, font=title_f)
        draw_outlined_text(((W-lw)//2, y), ln, title_f, (255,255,0,255))
        _, _, _, bh = d.textbbox((0,0), ln, font=title_f)
        y += bh + LINE_SPACING

    f_txt = f"Estreno en España: {fecha}" if fecha else ""
    f_txt = wrap_fit(d, f_txt, small_f, int(W*0.94))
    fw = d.textlength(f_txt, font=small_f)
    y_date = H - BOTTOM_MARGIN + int(BOTTOM_MARGIN*0.5) - small_f.size//2
    draw_outlined_text(((W-fw)//2, y_date), f_txt, small_f, (255,255,0,255))

    # Guardar para inspección
    ov_path = STATE / f"overlay_test_{tmdb_id}.png"
    ov.save(ov_path, "PNG")
    logging.info(f"Overlay guardado en: {ov_path}")

    return ov

def main(tmdb_id: str, title: str, fecha: str) -> Image.Image:
    return make_overlay_image(title, fecha, tmdb_id)