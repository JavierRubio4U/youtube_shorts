# 📘 MANUAL DE USO - GENERADOR DE SHORTS DE CINE

Este proyecto automatiza la creación de YouTube Shorts sobre cine. Busca películas, investiga curiosidades ("Salseo"), decide la mejor estrategia de venta, genera guiones con personalidad ("La Sinóptica Gamberra"), narra con voces neuronales de Google y edita el vídeo final.

---

## 🚀 1. Flujo de Trabajo Principal

Estos son los comandos que usarás habitualmente para crear contenido.

### A. Modo Automático ("El Piloto Automático")
El sistema elige la mejor película trending, la investiga y publica el vídeo sin tu intervención.

* **Comando:**
    ```powershell
    python scripts/publish.py
    ```
* **¿Qué hace?**
    1.  Ejecuta `find.py`: Busca candidatas y aplica **Deep Research** para decidir la estrategia (Actor, Director, Curiosidad o Trama).
    2.  Descarga Tráiler y Póster.
    3.  Extrae los clips más dinámicos.
    4.  Genera el Guion (con personalidad gamberra) y el Audio (Google Neural2 con pausas dramáticas).
    5.  Edita el MP4 vertical.
    6.  Sube a YouTube Shorts.

### B. Modo Manual ("El Francotirador")
Tú eliges una película específica y el sistema hace el resto del trabajo sucio.

* **Comando:**
    ```powershell
    python scripts/manual_publish.py
    ```
* **¿Qué hace?**
    1.  Te pide el **Nombre** y **Año** de la película.
    2.  Busca el tráiler oficial en YouTube.
    3.  Ejecuta el **Deep Research** para encontrar el gancho perfecto.
    4.  Continúa con todo el proceso de edición y subida igual que el modo automático.

---

## 🛠️ 2. Herramientas de Análisis y Pruebas

Útiles para verificar datos antes de crear el vídeo o para solucionar problemas.

### C. Solo Buscar y Analizar (`find.py`)
Ejecútalo si quieres ver qué película elegiría el sistema y qué cotilleo ha encontrado, pero sin descargar ni editar nada.

* **Comando:**
    ```powershell
    python scripts/find.py
    ```
* **Resultado:**
    * Genera el archivo de estado: `output/state/next_release.json`.
    * Muestra en consola la **"Ficha de Salseo"**: Estrategia elegida, Curiosidad, Referencia al Actor, etc.

### D. Probar Voz y Narración (`test_google_voice.py`)
Genera un audio de prueba con la sinopsis actual para verificar que la API de Google funciona y que el ritmo (SSML) es correcto.

* **Comando:**
    ```powershell
    python test/test_google_voice.py
    ```
    *(Nota: Si guardaste el script dentro de la carpeta `scripts`, usa `python scripts/test_google_voice.py`)*
* **Resultado:**
    * Crea un archivo MP3 en la carpeta `output/`.

### E. Regenerar Solo el Vídeo (`build_short.py`)
Si ya tienes la película descargada (está en `next_release.json`) pero quieres cambiar la edición, la música o el guion sin volver a buscarla.

* **Comando:**
    ```powershell
    python scripts/build_short.py
    ```
### F. Listar Modelos de IA Disponibles (`list_models.py`)
Consulta a la API de Google para ver qué modelos de Gemini tienes activos y disponibles con tu clave actual. Útil si quieres actualizar la configuración a un modelo más nuevo o ligero.

* **Comando:**
    ```powershell
    python test/list_models.py
    ```
* **Resultado:**
    * Imprime en consola una lista de los modelos compatibles (ej: `gemini-1.5-flash`, `gemini-pro`) junto con sus IDs exactos para usar en el código.
---

## 📂 3. Archivos Clave y Configuración

* **`config/`**:
    * `google_api_key.txt`: Tu llave para Gemini (IA) y Google TTS (Voz).
    * `tmdb_api_key.txt`: Tu llave para los datos de las películas.
* **`scripts/ai_narration.py`**:
    * Define la personalidad de la IA.
    * Configura los límites de palabras (45-60).
    * Controla el **SSML** (las pausas dramáticas y la velocidad del audio).
* **`scripts/movie_utils.py`**:
    * El "cerebro" investigador. Contiene el prompt del **Deep Research** que decide el ángulo de venta.
* **`output/state/next_release.json`**:
    * El archivo temporal que contiene toda la información de la película que se está procesando actualmente.

---

## ⚠️ 4. Solución de Problemas

1.  **Error `403 PERMISSION_DENIED` (Google TTS):**
    * Significa que tu API Key no tiene permiso para usar la voz. Ve a la consola de Google Cloud > Credenciales > Restricciones y activa **"Cloud Text-to-Speech API"**.
2.  **El vídeo se sube pero no es un Short:**
    * Verifica que la duración sea menor a 60 segundos. El script actual corta el guion para asegurar unos 30-40 segundos, por lo que no debería pasar.