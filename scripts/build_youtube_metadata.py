# scripts/build_youtube_metadata.py
# scripts/build_youtube_metadata.py
import json
from pathlib import Path
import logging
import ollama
import re # <-- AÑADIDO POR SEGURIDAD

# --- Configuración ---
ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "output" / "state"
SEL_FILE = STATE / "next_release.json"
META_FILE = STATE / "youtube_metadata.json"

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

# --- Constantes de IA ---
# CAMBIO: Modelo final establecido tras las pruebas.
OLLAMA_MODEL = 'qwen3:14b' 

# --- Función de traducción de título ---
def _translate_title_with_ai(title: str) -> str | None:
    """Usa Ollama para traducir un título con el prompt 'blindado' optimizado."""
    
    # CAMBIO: Usando el prompt "blindado" final para títulos.
    prompt = f"""
    Eres un experto en la localización de títulos de películas para el mercado de España.
    Tu ÚNICA tarea es traducir el siguiente título de película al castellano.

    **Reglas Inquebrantables:**
    1.  **FORMATO DE SALIDA**: SOLO devolverás el texto del título traducido. NADA MÁS. Está terminantemente prohibido incluir explicaciones, comentarios, razonamientos o cualquier tipo de etiqueta como `<think>`.
    2.  **LÓGICA DE TRADUCCIÓN**: TRADUCE SIEMPRE el título, a menos que sea una marca o franquicia mundialmente famosa que NUNCA se traduce en España (ej: Star Wars, Pulp Fiction, Avatar). En caso de duda, la acción por defecto es TRADUCIR.
    3.  **SUBTÍTULOS**: Si el título contiene ':', mantén la parte principal y traduce solo el subtítulo.

    **Título a traducir:** "{title}"
    """
    try:
        logging.info(f"Traduciendo título '{title}' con el modelo '{OLLAMA_MODEL}'...")
        response = ollama.chat(model=OLLAMA_MODEL, messages=[{'role': 'user', 'content': prompt}])
        translation = response['message']['content'].strip().strip('"')
        
        # CAMBIO: Limpieza de seguridad anti-<think>
        if '<think>' in translation:
            translation = re.sub(r'<think>.*</think>', '', translation, flags=re.DOTALL).strip()
            
        logging.info(f"Título traducido como: '{translation}'")
        return translation
    except Exception as e:
        logging.error(f"Error al contactar con el modelo de IA Ollama: {e}")
        return None


# --- Función Principal ---
def main():
    if not SEL_FILE.exists():
        logging.error("Falta next_release.json. Ejecuta select_next_release.py primero.")
        return

    sel = json.loads(SEL_FILE.read_text(encoding="utf-8"))
    
    original_title = sel.get("titulo", "Sin Título")
    
    # --- PASO DE TRADUCCIÓN (AÑADIDO) ---
    translated_title = _translate_title_with_ai(original_title)
    # Si la traducción falla, usamos el título original como fallback
    final_title = translated_title if translated_title else original_title

    year = sel.get("fecha_estreno", "N/A").split('-')[0]
    
    # --- Reemplazar con este bloque ---

    # Obtener la primera plataforma y la fecha
    plataforma_principal = sel.get("platforms", ["TBD"])[0]
    fecha_estreno_str = sel.get("fecha_estreno", "").replace('-', '/') # Formatear fecha

    # Construir el título final con el formato anterior
    youtube_title = f"#{sel.get('titulo')} Trailer - {plataforma_principal} Con Fecha de Estreno {fecha_estreno_str}"

    # --- Fin del bloque para reemplazar ---

    plataformas = sel.get("platforms", [])
    if plataformas:
        plataformas_str = ' '.join([f"#{p.replace(' ', '')}" for p in plataformas])
    else:
        plataformas_str = "#Estrenos"

    description = (
        f"Descubre el tráiler oficial de '{final_title}', la película del {year}. "
        f"Aquí tienes toda la información sobre su estreno y dónde verla.\n\n"
        f"► Título: {final_title}\n"
        f"► Año de estreno: {year}\n"
        f"► Sinopsis: {sel.get('sinopsis', 'Próximamente más detalles.')}\n\n"
        f"► Plataformas: {', '.join(plataformas)}\n\n"
        f"¡No te pierdas las últimas novedades y tráilers de cine y series!\n\n"
        f"#trailer #tráilerespañol #{final_title.replace(' ', '').replace(':', '')} {plataformas_str}"
    )

    keywords = [
        "tráiler", "trailer", "tráiler oficial", "tráiler español", "película", "cine", "estreno",
        final_title, f"{final_title} trailer", f"{final_title} pelicula", year
    ] + sel.get("generos", []) + sel.get("reparto_top", [])

    metadata = {
        "tmdb_id": sel.get("tmdb_id"),
        "title": youtube_title,
        "description": description.strip(),
        "tags": sorted(list(set(kw.lower() for kw in keywords if kw))),
        "privacyStatus": "private",
        "madeForKids": False,
        "categoryId": "1"  # Film & Animation
    }

    META_FILE.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    logging.info(f"✅ Metadata de YouTube guardada en: {META_FILE}")
    logging.info(f"   - Título final: {youtube_title}")

if __name__ == "__main__":
    main()