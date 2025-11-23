# Nuevo archivo: scripts/gemini_config.py

"""
Propósito: centralizar la configuración del modelo de IA Gemini
para evitar importaciones circulares entre scripts.

Todos los módulos del proyecto deberán importar el modelo desde aquí:

    from gemini_config import GEMINI_MODEL

Esto elimina la dependencia mutua entre publish.py, find.py y los demás.
"""

# --- Constante global única ---
# GEMINI_MODEL = "gemini-2.5-pro"
GEMINI_MODEL = "gemini-3-pro-preview"