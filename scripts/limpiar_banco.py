import pandas as pd
import os
import glob
import logging
import warnings

# Configuración de Logs
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger()
warnings.filterwarnings('ignore')

def procesar_datos_banco():
    # --- CONFIGURACIÓN ---
    input_dir = os.path.join(os.path.expanduser('~'), 'Downloads')
    
    # Destino final
    output_dir = r'E:\Contabilidad'
    os.makedirs(output_dir, exist_ok=True)
    
    patron_busqueda = os.path.join(input_dir, '*Movimientos de cuenta*.xls')
    
    logger.info("INICIO DEL PROCESO")
    logger.info(f"Leyendo de: {input_dir}")
    
    lista_ficheros = glob.glob(patron_busqueda)
    
    if not lista_ficheros:
        logger.error("No se encontraron ficheros.")
        return

    fichero_mas_reciente = max(lista_ficheros, key=os.path.getctime)
    logger.info(f"Fichero seleccionado: {os.path.basename(fichero_mas_reciente)}")

    try:
        df = None
        
        # --- CARGA (Soporte para falso XLS/HTML) ---
        try:
            df = pd.read_excel(fichero_mas_reciente, skiprows=10, engine='xlrd')
        except Exception:
            logger.warning("Activando Plan B (HTML)...")
            dfs = pd.read_html(fichero_mas_reciente)
            if dfs:
                df = dfs[0]
                df = df.iloc[10:] 
                df.columns = df.iloc[0]
                df = df[1:].reset_index(drop=True)
            else:
                raise ValueError("No se encontraron tablas.")

        # --- LIMPIEZA ---
        df.columns = df.columns.astype(str).str.strip()
        indices_a_borrar = [0, 2, 4, 6, 8]
        if df.shape[1] > 8:
            df.drop(df.columns[indices_a_borrar], axis=1, inplace=True)

        # --- FECHAS Y GUARDADO ---
        col_fecha = 'Fecha Operación'

        if col_fecha in df.columns:
            # Convertir fecha
            df[col_fecha] = pd.to_datetime(df[col_fecha], dayfirst=True, errors='coerce')
            df = df.dropna(subset=[col_fecha])
            
            if not df.empty:
                # Usamos la fecha MÁXIMA para el nombre
                fecha_para_nombre = df[col_fecha].max()
                
                # --- CAMBIO AQUÍ: %Y (4 dígitos) ---
                # Ejemplo: Enero 2026 -> 202601.csv
                nombre_fichero_salida = fecha_para_nombre.strftime('%Y%m') + '.csv'
                
                ruta_salida = os.path.join(output_dir, nombre_fichero_salida)

                logger.info(f"Fecha detectada para nombre: {fecha_para_nombre.date()}")
                logger.info(f"Guardando fichero como: {nombre_fichero_salida}")
                
                df.to_csv(ruta_salida, sep=',', index=False, encoding='utf-8-sig')
                
                logger.info(f"✅ FINALIZADO. Guardado en: {ruta_salida}")
            else:
                logger.error("Error: Todas las fechas son inválidas.")
        else:
            logger.error(f"No existe la columna '{col_fecha}'.")

    except Exception as e:
        logger.error(f"Error crítico: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    procesar_datos_banco()