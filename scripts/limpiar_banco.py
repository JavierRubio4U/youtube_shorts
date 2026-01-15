import pandas as pd
import os
import glob
import logging
import warnings
import csv

# --- CONFIGURACIÓN ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s', datefmt='%H:%M:%S')
logger = logging.getLogger()
warnings.filterwarnings('ignore')

def arreglar_importe_definitivo(valor):
    if pd.isna(valor): return ""
    try:
        val_float = float(valor)
    except ValueError:
        return str(valor)
    if val_float.is_integer():
        return str(int(val_float))
    else:
        return "{:.2f}".format(val_float).replace('.', ',')

def procesar_datos_banco():
    # --- RUTAS ---
    input_dir = os.path.join(os.environ['USERPROFILE'], 'Downloads')
    # input_dir = r'C:\Users\javier.rubio\Downloads' 
    output_dir = r'E:\Contabilidad'
    
    os.makedirs(output_dir, exist_ok=True)
    
    patron = os.path.join(input_dir, '*Movimientos de cuenta*.xls')
    ficheros = glob.glob(patron)
    
    if not ficheros:
        logger.error("No hay ficheros.")
        return

    fichero_reciente = max(ficheros, key=os.path.getctime)
    logger.info(f"Procesando: {os.path.basename(fichero_reciente)}")

    try:
        # LEER EXCEL (dtype=str es importante para capturar el dato crudo)
        try:
            df = pd.read_excel(fichero_reciente, skiprows=10, engine='xlrd', dtype=str)
        except Exception:
            dfs = pd.read_html(fichero_reciente, decimal=',', thousands='.')
            df = dfs[0]
            if len(df) > 10:
                df = df.iloc[10:].reset_index(drop=True)
                df.columns = df.iloc[0]
                df = df[1:].reset_index(drop=True)
        
        df.columns = df.columns.astype(str).str.strip()
        
        # BORRAR COLUMNAS SOBRANTES
        indices_borrar = [0, 2, 4, 6, 8]
        cols_borrar = [df.columns[i] for i in indices_borrar if i < len(df.columns)]
        if cols_borrar: df.drop(columns=cols_borrar, inplace=True)

        # --- APLICAR CORRECCIÓN ---
        for col in ['Importe', 'Saldo']:
            if col in df.columns:
                df[col] = df[col].apply(arreglar_importe_definitivo)

        # GESTIÓN FECHAS (DD/MM/YY)
        col_fecha = 'Fecha Operación'
        if col_fecha not in df.columns:
            posibles = [c for c in df.columns if 'Fecha' in c]
            col_fecha = posibles[0] if posibles else None

        if col_fecha:
            df['_dt'] = pd.to_datetime(df[col_fecha], dayfirst=True, errors='coerce')
            df = df.dropna(subset=['_dt'])
            
            if not df.empty:
                # Nombre YYYYMM
                fecha_ref = df['_dt'].max()
                nombre_fichero = fecha_ref.strftime('%Y%m') + '.csv'
                ruta_salida = os.path.join(output_dir, nombre_fichero)
                
                # Formato columna fecha visual: DD/MM/YY
                df[col_fecha] = df['_dt'].dt.strftime('%d/%m/%y')
                
                if 'Fecha Valor' in df.columns:
                    df['Fecha Valor'] = pd.to_datetime(df['Fecha Valor'], dayfirst=True, errors='coerce').dt.strftime('%d/%m/%y')

                df.drop(columns=['_dt'], inplace=True)

                logger.info(f"Guardando: {ruta_salida}")
                
                # GUARDADO FINAL (Sep=, Quote=ALL)
                df.to_csv(ruta_salida, sep=',', index=False, encoding='utf-8-sig', quoting=csv.QUOTE_ALL)
                
                logger.info("✅ FINALIZADO.")
            else:
                logger.error("Error: Fechas inválidas.")

    except Exception as e:
        logger.error(f"Error: {e}")

if __name__ == "__main__":
    procesar_datos_banco()