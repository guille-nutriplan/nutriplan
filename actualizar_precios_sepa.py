"""
actualizar_precios_sepa.py
Script LOCAL — correr desde tu máquina (IP argentina).

Descarga SEPA, procesa los precios medianos por grupo y provincia,
guarda data/precios_sepa.json y hace commit+push automático.

Uso:
    py actualizar_precios_sepa.py

El archivo JSON resultante queda en el repo → Railway lo lee automáticamente.
Recomendado: correr una vez por semana (los precios SEPA se actualizan semanalmente).
"""

import sys
import json
import subprocess
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent))

from data.sepa_client import (
    _url_del_dia, _descargar_y_filtrar, _procesar_precios,
    PROVINCIAS, _precios_referencia,
)

OUTPUT_JSON = Path('data/precios_sepa.json')

# Provincias para las que calculamos precios específicos
# (las más pobladas + Neuquén por contexto del usuario)
PROVINCIAS_TARGET = ['AR-B', 'AR-C', 'AR-X', 'AR-S', 'AR-Q', 'AR-M', 'AR-T']


def procesar_todas_las_provincias(df_raw):
    """Calcula precios medianos nacionales y por provincia target."""
    resultado = {}

    # Nacional (todas las provincias)
    print("  Calculando precios nacionales...")
    df_nacional = _procesar_precios(df_raw, provincia_codigo=None)
    resultado['nacional'] = {
        row['GRUPO']: round(float(row['PRECIO_MEDIANA_100G']), 2)
        for _, row in df_nacional.iterrows()
    }
    print(f"  ✓ Nacional: {len(resultado['nacional'])} grupos")

    # Por provincia
    for cod in PROVINCIAS_TARGET:
        nombre = PROVINCIAS.get(cod, cod)
        print(f"  Calculando precios {nombre}...")
        try:
            df_prov = _procesar_precios(df_raw, provincia_codigo=cod)
            resultado[cod] = {
                row['GRUPO']: round(float(row['PRECIO_MEDIANA_100G']), 2)
                for _, row in df_prov.iterrows()
            }
            print(f"  ✓ {nombre}: {len(resultado[cod])} grupos")
        except Exception as e:
            print(f"  ⚠ {nombre}: error ({e}), usando nacional")
            resultado[cod] = resultado['nacional'].copy()

    return resultado


def guardar_json(precios: dict, url_dia: str) -> dict:
    """Guarda los precios en data/precios_sepa.json."""
    datos = {
        'actualizado':  datetime.now().strftime('%Y-%m-%dT%H:%M:%S'),
        'fuente':       'SEPA datos.produccion.gob.ar',
        'archivo_sepa': url_dia,
        'precios':      precios,
    }
    OUTPUT_JSON.parent.mkdir(exist_ok=True)
    OUTPUT_JSON.write_text(
        json.dumps(datos, ensure_ascii=False, indent=2),
        encoding='utf-8'
    )
    print(f"\n✓ Guardado en {OUTPUT_JSON}")
    print(f"  Grupos: {list(precios['nacional'].keys())}")
    return datos


def git_push(mensaje: str):
    """Hace commit y push del JSON actualizado."""
    try:
        subprocess.run(['git', 'add', str(OUTPUT_JSON)], check=True)
        subprocess.run(['git', 'commit', '-m', mensaje], check=True)
        subprocess.run(['git', 'push'], check=True)
        print("✓ Push a GitHub exitoso — Railway actualizará automáticamente")
    except subprocess.CalledProcessError as e:
        print(f"⚠ Error en git: {e}")
        print("  Podés hacer el push manualmente con:")
        print(f"  git add data/precios_sepa.json && git commit -m '{mensaje}' && git push")


def main():
    print("═" * 60)
    print("  NutriPlan — Actualización de precios SEPA")
    print("═" * 60)
    print(f"  Fecha: {datetime.now().strftime('%d/%m/%Y %H:%M')}\n")

    # Descargar SEPA
    url, nombre_dia = _url_del_dia()
    print(f"Descargando SEPA {nombre_dia}...")
    print("(puede tardar 2-3 minutos según tu conexión)\n")

    try:
        df_raw = _descargar_y_filtrar(url, nombre_dia)
    except Exception as e:
        print(f"\n✗ Error descargando SEPA: {e}")
        print("\n¿Querés guardar los precios de referencia en su lugar? (s/n): ", end='')
        if input().strip().lower() == 's':
            df_ref = _precios_referencia()
            precios = {
                'nacional': {
                    row['GRUPO']: float(row['PRECIO_MEDIANA_100G'])
                    for _, row in df_ref.iterrows()
                }
            }
            # Copiar nacional a todas las provincias
            for cod in PROVINCIAS_TARGET:
                precios[cod] = precios['nacional'].copy()
            guardar_json(precios, 'referencia_local')
            git_push("chore: actualizar precios de referencia")
        return

    # Procesar precios por provincia
    print("\nProcesando precios por provincia...")
    precios = procesar_todas_las_provincias(df_raw)

    # Liberar memoria del raw
    del df_raw

    # Guardar JSON
    datos = guardar_json(precios, nombre_dia)

    # Resumen
    print("\n─── Precios nacionales ($/100g) ─────────────────────")
    for grupo, precio in sorted(precios['nacional'].items()):
        print(f"  {grupo:<15} ${precio:>8.2f}")

    # Git push
    fecha_str = datetime.now().strftime('%d/%m/%Y')
    git_push(f"data: precios SEPA actualizados {fecha_str}")

    print("\n" + "═" * 60)
    print("  ✓ Proceso completado")
    print("  Railway usará los nuevos precios en el próximo deploy")
    print("═" * 60)


if __name__ == '__main__':
    main()
