"""
sepa_client.py
Descarga y procesa la base de precios SEPA del gobierno argentino.

Fuente: https://datos.produccion.gob.ar/dataset/sepa-precios
Actualización: diaria
Licencia: Creative Commons Attribution 4.0

Los archivos SEPA son CSVs con columnas:
  id_comercio, id_bandera, id_sucursal, id_producto,
  productos_descripcion, productos_precio_lista,
  productos_precio_referencia, fecha

El mapeo SEPA → grupo nutricional se hace por palabras clave en
productos_descripcion. No es perfecto pero da una estimación razonable.
"""

import requests
import pandas as pd
import numpy as np
import re
from pathlib import Path
from datetime import datetime, timedelta
import json

# ─── URL de descarga SEPA (datos abiertos) ────────────────────────────────────
SEPA_BASE_URL = "https://datos.produccion.gob.ar/dataset/sepa-precios/resource"

# El dataset se actualiza diariamente. Descargamos el CSV más reciente.
# La URL tiene el formato: https://datos.produccion.gob.ar/dataset/sepa-precios
SEPA_DATASET_URL = "https://datos.produccion.gob.ar/api/3/action/package_show?id=sepa-precios"

# Caché local (evita re-descargar en cada ejecución)
CACHE_DIR = Path(__file__).parent / "cache"
CACHE_DIR.mkdir(exist_ok=True)

# ─── Provincias argentinas ────────────────────────────────────────────────────
PROVINCIAS = {
    'AR-B': 'Buenos Aires',
    'AR-C': 'CABA',
    'AR-K': 'Catamarca',
    'AR-H': 'Chaco',
    'AR-U': 'Chubut',
    'AR-X': 'Córdoba',
    'AR-W': 'Corrientes',
    'AR-E': 'Entre Ríos',
    'AR-P': 'Formosa',
    'AR-Y': 'Jujuy',
    'AR-L': 'La Pampa',
    'AR-F': 'La Rioja',
    'AR-M': 'Mendoza',
    'AR-N': 'Misiones',
    'AR-Q': 'Neuquén',
    'AR-R': 'Río Negro',
    'AR-A': 'Salta',
    'AR-J': 'San Juan',
    'AR-D': 'San Luis',
    'AR-Z': 'Santa Cruz',
    'AR-S': 'Santa Fe',
    'AR-G': 'Santiago del Estero',
    'AR-V': 'Tierra del Fuego',
    'AR-T': 'Tucumán',
}

# Invertir para búsqueda por nombre
PROVINCIAS_INV = {v.upper(): k for k, v in PROVINCIAS.items()}

# ─── Mapeo producto SEPA → grupo nutricional ──────────────────────────────────
# Cada entrada: (palabras_clave_regex, grupo_nutricional)
# El orden importa: primera coincidencia gana
KEYWORDS_GRUPO = [
    # Carnes
    (r'\b(pollo|pechuga|muslo|cuarto trasero|suprema)\b', 'Aves'),
    (r'\b(carne|bife|asado|costillar|paleta|peceto|rosbif|nalga|cuadrada|cuadril|lomo|milanesa)\b', 'Carnes'),
    (r'\b(cerdo|bondiola|carré|pechito de cerdo)\b', 'Carnes'),
    (r'\b(fiambre|jamon|salchicha|mortadela|chorizo|salame|paleta)\b', 'Embutidos'),
    (r'\b(merluza|pescado|atún|sardina|calamar|langostino|caballa)\b', 'Pescados'),
    # Lácteos
    (r'\b(leche|yogur|queso|crema|manteca|caseína)\b', 'Lacteos'),
    # Huevos
    (r'\b(huevo|huevos)\b', 'Huevos'),
    # Cereales / panificados
    (r'\b(pan|harina|arroz|fideos|pasta|spaghetti|macarrones|tallarines|polenta|avena|maicena|galleta|galletita|cereal)\b', 'Cereales'),
    # Legumbres
    (r'\b(lenteja|garbanzo|poroto|arvejas|soja|habas)\b', 'Leguminosas'),
    # Frutas y verduras
    (r'\b(papa|zapallo|calabaza|choclo|zanahoria|tomate|lechuga|cebolla|ajo|espinaca|acelga|brócoli|coliflor|berenjena|pepino)\b', 'Hortalizas'),
    (r'\b(manzana|naranja|mandarina|banana|pera|durazno|damasco|uva|limón|pomelo|kiwi|frutilla)\b', 'Frutas'),
    # Aceites y grasas
    (r'\b(aceite|margarina)\b', 'Aceites'),
    # Azúcares
    (r'\b(azúcar|azucar|miel|dulce|mermelada|chocolate)\b', 'Azucares'),
    # Frutos secos
    (r'\b(almendra|nuez|maní|maní|avellana|castaña|pistacho)\b', 'FrutosSecos'),
]


def _mapear_grupo(descripcion: str) -> str | None:
    """Asigna un grupo nutricional a un producto SEPA por su descripción."""
    desc = descripcion.lower()
    for patron, grupo in KEYWORDS_GRUPO:
        if re.search(patron, desc, re.IGNORECASE):
            return grupo
    return None


def obtener_precios_sepa(
    provincia_codigo: str | None = None,
    provincia_nombre: str | None = None,
    forzar_descarga: bool = False,
    max_age_horas: int = 24,
) -> pd.DataFrame:
    """
    Obtiene precios SEPA filtrados por provincia.

    Parameters
    ----------
    provincia_codigo : str, optional
        Código de provincia, ej: 'AR-Q' para Neuquén
    provincia_nombre : str, optional
        Nombre de provincia, ej: 'Neuquén'. Alternativa al código.
    forzar_descarga : bool
        Si True, ignora caché y re-descarga
    max_age_horas : int
        Edad máxima del caché en horas antes de re-descargar

    Returns
    -------
    pd.DataFrame con columnas: GRUPO, PRECIO_MEDIANA_100G, N_PRODUCTOS, FUENTE
    """
    # Resolver provincia
    if provincia_nombre and not provincia_codigo:
        codigo = PROVINCIAS_INV.get(provincia_nombre.upper().strip())
        if not codigo:
            # Búsqueda parcial
            for nombre_key, cod in PROVINCIAS_INV.items():
                if provincia_nombre.upper() in nombre_key:
                    codigo = cod
                    break
        if not codigo:
            raise ValueError(f"Provincia '{provincia_nombre}' no reconocida. "
                           f"Opciones: {list(PROVINCIAS.values())}")
        provincia_codigo = codigo

    cache_file = CACHE_DIR / f"precios_{provincia_codigo or 'nacional'}.parquet"

    # Verificar caché
    if not forzar_descarga and cache_file.exists():
        edad = datetime.now() - datetime.fromtimestamp(cache_file.stat().st_mtime)
        if edad < timedelta(hours=max_age_horas):
            print(f"✓ Usando caché SEPA ({edad.seconds // 3600}h {(edad.seconds % 3600) // 60}m de antigüedad)")
            return pd.read_parquet(cache_file)

    print("⬇ Descargando datos SEPA...")
    try:
        df_sepa = _descargar_sepa(provincia_codigo)
        df_precios = _procesar_precios(df_sepa)
        df_precios.to_parquet(cache_file)
        return df_precios
    except Exception as e:
        print(f"⚠ Error descargando SEPA: {e}")
        print("  Usando precios de referencia locales como fallback.")
        return _precios_referencia()


def _descargar_sepa(provincia_codigo: str | None) -> pd.DataFrame:
    """Descarga y filtra el CSV SEPA del día actual."""
    # Obtener la URL del recurso más reciente via API CKAN
    resp = requests.get(SEPA_DATASET_URL, timeout=30)
    resp.raise_for_status()
    recursos = resp.json()['result']['resources']

    # El recurso de hoy (o el más reciente disponible)
    # Los recursos están ordenados por fecha en el nombre
    hoy = datetime.now().strftime('%Y-%m-%d')
    csv_urls = []
    for r in recursos:
        if r.get('format', '').upper() == 'CSV':
            csv_urls.append((r.get('name', ''), r['url']))

    if not csv_urls:
        raise RuntimeError("No se encontraron recursos CSV en el dataset SEPA")

    # Usar el primer CSV disponible (más reciente)
    nombre, url = csv_urls[0]
    print(f"  Recurso SEPA: {nombre}")

    # Descargar en chunks para archivos grandes (~100MB+)
    chunks = []
    with requests.get(url, stream=True, timeout=60) as r:
        r.raise_for_status()
        for chunk in pd.read_csv(
            r.raw,
            chunksize=50000,
            usecols=['id_sucursal', 'productos_descripcion',
                     'productos_precio_lista'],
            dtype=str,
            encoding='utf-8',
            on_bad_lines='skip'
        ):
            # Filtrar por provincia si se especificó
            if provincia_codigo:
                # El campo id_sucursal tiene formato: id_comercio-id_bandera-id_sucursal
                # Las sucursales de la provincia están en el CSV de sucursales
                # Por ahora filtramos por código embebido (workaround)
                # TODO: cruzar con CSV de sucursales para filtro por provincia exacto
                pass
            chunks.append(chunk)

    return pd.concat(chunks, ignore_index=True)


def _procesar_precios(df: pd.DataFrame) -> pd.DataFrame:
    """
    Agrupa precios SEPA por grupo nutricional y calcula la mediana.
    Normaliza a precio por 100g.
    """
    df['GRUPO'] = df['productos_descripcion'].apply(_mapear_grupo)
    df = df.dropna(subset=['GRUPO'])
    df['precio'] = pd.to_numeric(df['productos_precio_lista'], errors='coerce')
    df = df.dropna(subset=['precio'])
    df = df[df['precio'] > 0]

    # Extraer gramos del nombre del producto para normalizar precio/100g
    # Ej: "Arroz Largo Fino La Campagnola 1kg" → 1000g
    def extraer_gramos(desc):
        patrones = [
            r'(\d+(?:[,\.]\d+)?)\s*kg',   # 1kg, 1.5kg
            r'(\d+(?:[,\.]\d+)?)\s*gr?',  # 500g, 500gr
        ]
        for pat in patrones:
            m = re.search(pat, str(desc), re.IGNORECASE)
            if m:
                val = float(m.group(1).replace(',', '.'))
                if 'k' in pat:
                    return val * 1000
                return val
        return 1000  # default: asume 1kg

    df['gramos'] = df['productos_descripcion'].apply(extraer_gramos)
    df['precio_100g'] = df['precio'] / df['gramos'] * 100

    # Eliminar outliers por grupo (percentil 5-95)
    resultado = []
    for grupo, gdf in df.groupby('GRUPO'):
        p5 = gdf['precio_100g'].quantile(0.05)
        p95 = gdf['precio_100g'].quantile(0.95)
        filtrado = gdf[(gdf['precio_100g'] >= p5) & (gdf['precio_100g'] <= p95)]
        resultado.append({
            'GRUPO': grupo,
            'PRECIO_MEDIANA_100G': filtrado['precio_100g'].median(),
            'N_PRODUCTOS': len(filtrado),
            'FUENTE': 'SEPA',
        })

    return pd.DataFrame(resultado)


def _precios_referencia() -> pd.DataFrame:
    """
    Precios de referencia en ARS (estimados, Oct 2024).
    Se usan como fallback cuando SEPA no está disponible.
    Actualizar periódicamente.
    """
    # Precios medianos por 100g en ARS, basados en canasta básica alimentaria
    PRECIOS_REF_2024 = {
        'Cereales':     180,   # Arroz, fideos, harina
        'Leguminosas':  150,   # Lentejas, porotos
        'Hortalizas':    80,   # Papa, cebolla, zanahoria
        'Frutas':       120,   # Manzana, naranja, banana
        'FrutosSecos':  800,   # Maní, almendras
        'Lacteos':      220,   # Leche, yogur, queso
        'Huevos':       180,   # Por 100g equivalente
        'Aceites':      350,   # Aceite girasol
        'Azucares':     130,   # Azúcar, dulces
        'Pescados':     400,   # Merluza, atún
        'Carnes':       650,   # Carne vacuna, cerdo
        'Embutidos':    700,   # Fiambres, embutidos
        'Aves':         350,   # Pollo
    }
    registros = [
        {
            'GRUPO': grupo,
            'PRECIO_MEDIANA_100G': precio,
            'N_PRODUCTOS': 0,
            'FUENTE': 'referencia_local',
        }
        for grupo, precio in PRECIOS_REF_2024.items()
    ]
    return pd.DataFrame(registros)


def aplicar_precios(df_alimentos: pd.DataFrame,
                    df_precios: pd.DataFrame) -> pd.DataFrame:
    """
    Une los precios SEPA con el DataFrame de alimentos por grupo.

    Parameters
    ----------
    df_alimentos : DataFrame de tabla_loader.cargar_tabla()
    df_precios   : DataFrame de obtener_precios_sepa()

    Returns
    -------
    df_alimentos con columnas PRECIO_100G y PRECIO_g completadas
    """
    precio_map = df_precios.set_index('GRUPO')['PRECIO_MEDIANA_100G'].to_dict()

    df_alimentos = df_alimentos.copy()
    df_alimentos['PRECIO_100G'] = df_alimentos['GRUPO'].map(precio_map)
    df_alimentos['PRECIO_g'] = df_alimentos['PRECIO_100G'] / 100.0

    sin_precio = df_alimentos['PRECIO_100G'].isna().sum()
    if sin_precio > 0:
        print(f"⚠ {sin_precio} alimentos sin precio asignado (grupos no mapeados)")

    return df_alimentos


if __name__ == '__main__':
    # Test básico
    df = _precios_referencia()
    print("Precios de referencia:")
    print(df.to_string(index=False))
