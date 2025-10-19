# scripts/build_youtube_metadata.py
import json
from pathlib import Path
import logging
import re
import google.generativeai as genai  
import sys  # CAMBIO: Necesario para sys.exit()
from gemini_config import GEMINI_MODEL
from datetime import datetime  


# --- Configuración ---
ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "output" / "state"
SEL_FILE = STATE / "next_release.json"
META_FILE = STATE / "youtube_metadata.json"
CONFIG_DIR = ROOT / "config"  # CAMBIO: Añadido para la clave de API

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

# --- Constantes de IA ---
# CAMBIO: Usamos el modelo de Gemini Pro
# GEMINI_MODEL = 'gemini-2.5-pro'

# --- Carga de la clave de API de Google ---
# CAMBIO: Añadido bloque de carga de la API Key de Gemini
try:
    GOOGLE_CONFIG_FILE = CONFIG_DIR / "google_api_key.txt"
    with open(GOOGLE_CONFIG_FILE, "r") as f:
        GOOGLE_API_KEY = f.read().strip()
    if not GOOGLE_API_KEY:
        raise ValueError("La clave de API de Google está vacía.")
    genai.configure(api_key=GOOGLE_API_KEY)
except (FileNotFoundError, ValueError) as e:
    logging.error(f"Error crítico al cargar la clave de API de Google: {e}. Asegúrate de que 'google_api_key.txt' existe en '/config' y no está vacío.")
    sys.exit(1)

# --- Función de traducción de título ---
def _translate_title_with_ai(title: str) -> str | None:
    """Usa Gemini para traducir un título con el prompt 'blindado' optimizado."""
    
    # El prompt es idéntico al que validamos en gemini.py, es perfecto para esta tarea.
    prompt = f"""
    Eres un experto en la localización de títulos de películas para el mercado de España.
    Tu ÚNICA tarea es traducir el siguiente título de película al castellano.

    **Reglas Inquebrantables:**
    1.  **FORMATO DE SALIDA**: SOLO devolverás el texto del título traducido. NADA MÁS. Está terminantemente prohibido incluir explicaciones, comentarios, razonamientos o cualquier tipo de texto adicional.
    2.  **LÓGICA DE TRADUCCIÓN**: TRADUCE SIEMPRE el título, a menos que sea una marca o franquicia mundialmente famosa que NUNCA se traduce en España (ej: Star Wars, Pulp Fiction, Avatar). En caso de duda, la acción por defecto es TRADUCIR.
    3.  **SUBTÍTULOS**: Si el título contiene ':', mantén la parte principal y traduce solo el subtítulo.

    **Título a traducir:** "{title}"
    """
    try:
        logging.info(f"Traduciendo título '{title}' con el modelo '{GEMINI_MODEL}'...")
        # CAMBIO: Lógica de generación con Gemini
        model = genai.GenerativeModel(GEMINI_MODEL)
        response = model.generate_content(prompt)
        translation = response.text.strip().strip('"')
            
        logging.info(f"Título traducido como: '{translation}'")
        return translation
    except Exception as e:
        logging.error(f"Error al contactar con la API de Gemini: {e}")
        return None


# --- Función Principal ---
def main():
    if not SEL_FILE.exists():
        logging.error("Falta next_release.json. Ejecuta select_next_release.py primero.")
        return

    sel = json.loads(SEL_FILE.read_text(encoding="utf-8"))
    
    original_title = sel.get("titulo", "Sin Título")
    
    # --- PASO DE TRADUCCIÓN (AHORA CON GEMINI) ---
    translated_title = _translate_title_with_ai(original_title)
    final_title = translated_title if translated_title else original_title

    # DESPUÉS
    fecha = sel.get("fecha_estreno", "N/A")

    year = "2025"  # Valor por defecto
    fecha_estreno_str = ""

    if fecha and fecha != "N/A":
        try:
            # 1. Aislamos la fecha (ej: '2025-10-23') para eliminar la hora si la hubiera
            date_part = fecha.split('T')[0]
            # 2. Convertimos el texto a un objeto de fecha real
            date_obj = datetime.strptime(date_part, '%Y-%m-%d')
            # 3. Formateamos la fecha al formato dd/MM/yy que necesitas
            fecha_estreno_str = date_obj.strftime('%d/%m/%y')
            # 4. Obtenemos el año de forma segura
            year = date_obj.strftime('%Y')
        except ValueError:
            # Si la fecha viene en un formato inesperado, lo registramos y continuamos sin ella
            logging.warning(f"Formato de fecha no válido: '{fecha}'. No se usará en el título.")
    
    # --- LÓGICA DE PLATAFORMA MEJORADA ---
    plataformas_dict = sel.get("platforms", {})
    streaming_platforms = plataformas_dict.get("streaming", [])
    plataforma_principal = streaming_platforms[0] if streaming_platforms else "Cine"
    
    # --- LÓGICA DE TÍTULO CON PAÍS DE ESTRENO ---
    # 1. Preparamos la plataforma con el país (si no es de España)
    pais_de_la_fecha = sel.get("pais_de_la_fecha")
    
    if pais_de_la_fecha and pais_de_la_fecha != "ES":
        # Ej: "Amazon Prime Video (US)"
        plataforma_con_pais = f"{plataforma_principal} ({pais_de_la_fecha})"
    else:
        # Ej: "Amazon Prime Video"
        plataforma_con_pais = plataforma_principal

    # 2. Construimos el título final con el nuevo formato
    if fecha_estreno_str:
        # Formato: Título - Plataforma (País) - Fecha
        youtube_title = f"{final_title} - {plataforma_con_pais} - {fecha_estreno_str}"
    else:
        # Si no hay fecha, no la incluimos
        youtube_title = f"{final_title} - {plataforma_con_pais}"
    # --- FIN DEL CAMBIO ---
    
    # Plataformas para desc/hashtags
    todas_las_plataformas = sorted(list(set(
        plataformas_dict.get("streaming", []) + 
        plataformas_dict.get("buy", []) + 
        plataformas_dict.get("rent", [])
    )))
    if todas_las_plataformas:
        plataformas_str_hashtags = ' '.join([f"#{p.replace(' ', '')}" for p in todas_las_plataformas])
        plataformas_str_desc = ', '.join(todas_las_plataformas)
    else:
        plataformas_str_hashtags = "#Estrenos #Cine"
        plataformas_str_desc = "Próximamente en cines"

    description = (
        f"Descubre el tráiler oficial de '{final_title}', la película del {year}. "
        f"Aquí tienes toda la información sobre su estreno y dónde verla.\n\n"
        f"► Título: {final_title}\n"
        f"► Año de estreno: {year}\n"
        f"► Sinopsis: {sel.get('sinopsis', 'Próximamente más detalles.')}\n\n"
        f"► Plataformas: {plataformas_str_desc}\n\n"  # ✅ Fix typo
        f"¡No te pierdas las últimas novedades y tráilers de cine y series!\n\n"
        f"#trailer #tráilerespañol #{final_title.replace(' ', '').replace(':', '')} {plataformas_str_hashtags}"
    )

    # Keywords mejorados
    keywords = [
        "tráiler", "trailer", "tráiler oficial", "tráiler español", "película", "cine", "estreno",
        final_title, f"{final_title} trailer", f"{final_title} pelicula"
    ]
    if year != "N/A":
        keywords.append(year)
    keywords += [g for g in sel.get("generos", []) if g]  # Asume añadido al payload
    keywords += [r for r in sel.get("reparto_top", []) if r]  # Asume añadido

    metadata = {
        "tmdb_id": sel.get("tmdb_id"),
        "title": youtube_title,
        "description": description.strip(),
        "tags": sorted(list(set(kw.lower() for kw in keywords if kw and kw != "N/A"))),
        "privacyStatus": "private",
        "madeForKids": False,
        "categoryId": "1"  # Film & Animation
    }

    META_FILE.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    logging.info(f"✅ Metadata de YouTube guardada en: {META_FILE}")
    logging.info(f"   - Título final: {youtube_title}")

if __name__ == "__main__":
    main()