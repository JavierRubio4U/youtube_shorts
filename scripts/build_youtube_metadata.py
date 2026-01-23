# scripts/build_youtube_metadata.py
import json
from pathlib import Path
import logging
import re
from google import genai 
import sys
from gemini_config import GEMINI_MODEL
from datetime import datetime  

# --- Configuración ---
ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "output" / "state"
SEL_FILE = STATE / "next_release.json"
META_FILE = STATE / "youtube_metadata.json"
CONFIG_DIR = ROOT / "config"

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

# --- Helper Key ---
def get_google_api_key():
    try:
        with open(CONFIG_DIR / "google_api_key.txt") as f:
            return f.read().strip()
    except Exception as e:
        logging.error(f"❌ Error al cargar google_api_key.txt: {e}")
        return None

# --- Función de traducción de título ---
def _translate_title_with_ai(title: str) -> str | None:
    """Usa Gemini para traducir un título con el prompt 'blindado' optimizado."""
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
        api_key = get_google_api_key()
        if not api_key: return None

        logging.info(f"Traduciendo título '{title}' con el modelo '{GEMINI_MODEL}'...")
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
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
    # translated_title = _translate_title_with_ai(original_title)
    # final_title = translated_title if translated_title else original_title
    final_title = original_title

    # DESPUÉS
    fecha = sel.get("fecha_estreno", "N/A")

    year = "2025"  # Valor por defecto
    fecha_estreno_str = ""

    if fecha and fecha != "N/A":
        try:
            date_part = fecha.split('T')[0]
            date_obj = datetime.strptime(date_part, '%Y-%m-%d')
            fecha_estreno_str = date_obj.strftime('%d/%m/%y')
            year = date_obj.strftime('%Y')
        except ValueError:
            logging.warning(f"Formato de fecha no válido: '{fecha}'. No se usará en el título.")
    
    # --- 🔴 INICIO DEL CAMBIO: LÓGICA DE PLATAFORMA SANITIZADA ---
    
    # 1. Plataforma detectada por IA desde el título
    ia_platform = sel.get("ia_platform_from_title")

    # 2. Plataformas de la API de TMDB
    plataformas_dict = sel.get("platforms", {})
    tmdb_streaming_platforms = plataformas_dict.get("streaming", [])

    plataforma_principal = "Cine" # Valor por defecto inicial

    # Lógica de decisión estricta
    usar_ia = False
    es_streaming_generico = False # Flag para saber si es streaming pero no sabemos cual

    if ia_platform and ia_platform != "Cine":
        low_p = ia_platform.lower()
        # Palabras prohibidas que indican duda o texto sucio
        bad_keywords = ["probable", "posible", "estimad", "unknown", "desconocid", "tba", "tbd", "check", "verificar"]
        
        if any(kw in low_p for kw in bad_keywords):
            logging.info(f"Plataforma IA '{ia_platform}' descartada por ser incierta.")
            usar_ia = False
            # Si dice probable, asumimos que al menos es streaming
            es_streaming_generico = True 
        elif low_p.strip() == "streaming":
            logging.info(f"Plataforma IA '{ia_platform}' descartada por ser genérica.")
            usar_ia = False
            es_streaming_generico = True
        else:
            usar_ia = True

    # Asignación final
    if usar_ia:
        plataforma_principal = ia_platform
        logging.info(f"Usando plataforma IA: {plataforma_principal}")
    elif tmdb_streaming_platforms:
        plataforma_principal = tmdb_streaming_platforms[0]
        logging.info(f"Usando plataforma TMDB (Fallback): {plataforma_principal}")
    elif es_streaming_generico:
        # Si la IA dijo Streaming/Probable pero TMDB no sabe nada, ponemos TBD en vez de Cine
        plataforma_principal = "TBD"
        logging.info("Plataforma incierta. Usando 'TBD'.")
    else:
        # Si no hay indicios de streaming, asumimos Cine
        plataforma_principal = "Cine"
        logging.info("Sin datos de plataforma. Usando 'Cine'.")
    
    # --- FIN LÓGICA DE PLATAFORMA ---
    
    # --- LÓGICA DE TÍTULO CON PAÍS DE ESTRENO ---
    pais_de_la_fecha = sel.get("pais_de_la_fecha")
    
    if pais_de_la_fecha and pais_de_la_fecha != "ES":
        plataforma_con_pais = f"{plataforma_principal} ({pais_de_la_fecha})"
    else:
        plataforma_con_pais = plataforma_principal

    # Construimos el título final
    if fecha_estreno_str:
        youtube_title = f"{final_title} - {plataforma_con_pais} - {fecha_estreno_str}"
    else:
        youtube_title = f"{final_title} - {plataforma_con_pais}"
    
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
        f"► Plataformas: {plataformas_str_desc}\n\n"
        f"¡No te pierdas las últimas novedades y tráilers de cine y series!\n\n"
        f"#trailer #tráilerespañol #{final_title.replace(' ', '').replace(':', '')} {plataformas_str_hashtags}"
    )

    keywords = [
        "tráiler", "trailer", "tráiler oficial", "tráiler español", "película", "cine", "estreno",
        final_title, f"{final_title} trailer", f"{final_title} pelicula"
    ]
    if year != "N/A":
        keywords.append(year)
    keywords += [g for g in sel.get("generos", []) if g]
    keywords += [r for r in sel.get("reparto_top", []) if r]

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