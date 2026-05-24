"""
diagnostico_sepa.py
Inspecciona la estructura del ZIP de SEPA sin procesar todo.
Correr ANTES de actualizar precios para ver columnas disponibles.
"""
import sys, io, zipfile, requests, pandas as pd
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from data.sepa_client import _url_del_dia, SEPA_URLS
from datetime import datetime

def inspeccionar():
    # Probar desde ayer hacia atrás
    dia_hoy = datetime.now().weekday()
    for delta in range(7):
        dia = (dia_hoy - delta) % 7
        url, nombre = SEPA_URLS[dia]
        print(f"\nProbando {nombre}...")
        try:
            headers = {'User-Agent': 'Mozilla/5.0 NutriPlan/2.0'}
            resp = requests.get(url, stream=True, timeout=300, headers=headers)
            resp.raise_for_status()

            # Descargar solo los primeros 30MB para inspección
            contenido = io.BytesIO()
            for chunk in resp.iter_content(1024*1024):
                contenido.write(chunk)
                if contenido.tell() > 30 * 1024 * 1024:
                    print(f"  (detenido en 30MB para diagnóstico)")
                    break
            resp.close()
            contenido.seek(0)

            with zipfile.ZipFile(contenido) as zf:
                print(f"  Entradas: {zf.namelist()}")

                for entry in zf.namelist():
                    if entry.endswith('/'):
                        continue
                    print(f"\n  → Abriendo: {entry}")

                    if entry.lower().endswith('.zip'):
                        try:
                            with zf.open(entry) as f2:
                                inner = io.BytesIO(f2.read())
                            with zipfile.ZipFile(inner) as zf2:
                                print(f"    ZIP interno contiene: {zf2.namelist()}")
                                for e2 in zf2.namelist():
                                    if not e2.endswith('/'):
                                        print(f"    → Leyendo: {e2}")
                                        with zf2.open(e2) as csv_f:
                                            # Intentar con distintos encodings
                                            for enc in ['utf-8', 'latin-1', 'cp1252', 'utf-8-sig']:
                                                try:
                                                    csv_f.seek(0)
                                                    df = pd.read_csv(csv_f, nrows=3,
                                                                     encoding=enc,
                                                                     on_bad_lines='skip')
                                                    print(f"    Encoding OK: {enc}")
                                                    print(f"    Columnas: {list(df.columns)}")
                                                    print(f"    Muestra:\n{df.to_string()}")
                                                    break
                                                except Exception as ex:
                                                    print(f"    {enc}: {ex}")
                                        break  # solo primer archivo
                        except Exception as e:
                            print(f"    Error abriendo ZIP interno: {e}")

                    elif entry.lower().endswith('.csv'):
                        with zf.open(entry) as csv_f:
                            for enc in ['utf-8', 'latin-1', 'cp1252']:
                                try:
                                    csv_f.seek(0) if hasattr(csv_f, 'seek') else None
                                    df = pd.read_csv(csv_f, nrows=3, encoding=enc,
                                                     on_bad_lines='skip')
                                    print(f"  Encoding: {enc}")
                                    print(f"  Columnas: {list(df.columns)}")
                                    print(f"  Muestra:\n{df.to_string()}")
                                    break
                                except Exception as ex:
                                    print(f"  {enc}: {ex}")
                    break  # solo primer archivo no-directorio

            break  # si llegamos acá, tuvimos éxito
        except Exception as e:
            print(f"  Error: {e}")

if __name__ == '__main__':
    inspeccionar()
