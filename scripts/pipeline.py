from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = ROOT / "scripts"
# permite importar módulos que están dentro de ./scripts
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import select_next_release
import download_assets

def run():
    print("▶ Paso 1: seleccionar siguiente…")
    sel = select_next_release.pick_next()
    if not sel:
        return None

    print("▶ Paso 2: descargar assets (vertical/letterbox, 8 backdrops)…")
    download_assets.main()  # sin flags; ya con defaults “buenos”

    # Opcional: metadata para YouTube si existe el módulo
    try:
        import build_youtube_metadata
    except ModuleNotFoundError:
        print("ℹ build_youtube_metadata.py no encontrado; paso opcional omitido.")
    else:
        print("▶ Paso 3: generar metadata de YouTube…")
        build_youtube_metadata.main()

    print("✅ Pipeline completado.")
    return sel

if __name__ == "__main__":
    run()
