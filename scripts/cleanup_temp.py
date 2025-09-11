# scripts/cleanup_temp.py
import os
import logging
from pathlib import Path
import shutil

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "output" / "state"
CLIPS_DIR = ROOT / "assets" / "video_clips"
TEMP_DIR = Path(os.environ.get('TEMP', os.path.join(os.path.expanduser("~"), 'AppData', 'Local', 'Temp')))

def cleanup_state():
    """Elimina archivos no JSON en output/state."""
    for file in STATE.glob("*"):
        if file.suffix not in ['.json']:
            try:
                if file.is_file():
                    file.unlink()
                    logging.info(f"Archivo temporal {file.name} eliminado de {STATE}")
            except Exception as e:
                logging.warning(f"No se pudo eliminar {file.name}: {e}")

def cleanup_video_clips():
    """Elimina clips temporales en assets/video_clips (opcional, si son temporales)."""
    if os.path.exists(CLIPS_DIR):
        shutil.rmtree(CLIPS_DIR)
        logging.info(f"Carpeta {CLIPS_DIR} eliminada.")
        CLIPS_DIR.mkdir(parents=True, exist_ok=True)

def cleanup_appdata_temp():
    """Elimina archivos temporales relacionados con el proyecto en AppData\Local\Temp."""
    for file in TEMP_DIR.glob("*"):
        if file.name.startswith("tmp") and (file.name.endswith(".mp4") or file.name.endswith(".jpg")):
            try:
                file.unlink()
                logging.info(f"Archivo temporal {file.name} eliminado de {TEMP_DIR}")
            except Exception as e:
                logging.warning(f"No se pudo eliminar {file.name}: {e}")

def main(force_all=False):
    cleanup_state()
    if force_all:
        cleanup_video_clips()
    cleanup_appdata_temp()
    logging.info("Limpieza completada.")

if __name__ == "__main__":
    main()