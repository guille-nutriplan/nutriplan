"""
sepa_client.py
Descarga y procesa la base de precios SEPA del gobierno argentino.

Fuente:   https://www.datos.gob.ar/dataset/produccion-precios-claros---base-sepa
Licencia: Creative Commons Attribution 4.0

Estructura actual (2025):
  7 archivos ZIP — uno por día de la semana — con URLs fijas.
  Cada ZIP contiene un CSV con ~12 millones de registros diarios.
  Se actualiza automáticamente cada semana.

Estrategia de descarga:
  - Streaming del ZIP → descompresión en memoria → filtrado por keywords
  - Solo se procesan las filas de alimentos (< 1% del total)
  - Resultado: caché de ~100KB en lugar de 4GB
  - Validez del caché: 24 horas
"""

import requests
import pandas as pd
import numpy as np
import re
import zipfile
import io
from pathlib import Path
from datetime import datetime, timedelta

# ─── URLs fijas por día de la semana ──────────────────────────────────────────
# Resource IDs estables — el gobierno los mantiene y actualiza el contenido
DATASET_ID = "6f47ec76-d1ce-4e34-a7e1-621fe9b1d0b5"
BASE_URL    = f"https://datos.produccion.gob.ar/dataset/{DATASET_ID}/resource"

SEPA_URLS = {
    0: (f"{BASE_URL}/0a9069a9-06e8-4f98-874d-da5578693290/download/sepa_lunes.zip",    "lunes"),
    1: (f"{BASE_URL}/9dc06241-cc83-44f4-8e25-c9b1636b8bc8/download/sepa_martes.zip",   "martes"),
    2: (f"{BASE_URL}/1e92cd42-4f94-4071-a165-62c4cb2ce23c/download/sepa_miercoles.zip","miercoles"),
    3: (f"{BASE_URL}/d076720f-a7f0-4af8-b1d6-1b99d5a90c14/download/sepa_jueves.zip",  "jueves"),
    4: (f"{BASE_URL}/91bc072a-4726-44a1-85ec-4a8467aad27e/download/sepa_viernes.zip",  "viernes"),
    5: (f"{BASE_URL}/b3c3da5d-213d-41e7-8d74-f23fda0a3c30/download/sepa_sabado.zip",   "sabado"),
    6: (f"{BASE_URL}/f8e75128-515a-436e-bf8d-5c63a62f2005/download/sepa_domingo.zip",  "domingo"),
}

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

PROVINCIAS_INV = {v.upper(): k for k, v in PROVINCIAS.items()}

# Mapeo código SEPA de provincia → código AR-X
# El CSV de SEPA usa id_bandera que codifica la cadena, y el campo
# id_sucursal que tiene formato: id_comercio-id_bandera-nro_sucursal
# La provincia está codificada en el nombre del archivo de sucursales.
# Como fallback, usamos el campo "provincia" si está disponible en el CSV,
# o filtramos por nombre de producto solamente (precios nacionales).

# ─── Mapeo keywords → grupo nutricional ──────────────────────────────────────
KEYWORDS_GRUPO = [
    (r'\b(pollo|pechuga|muslo|cuarto trasero|suprema de pollo)\b', 'Aves'),
    (r'\b(carne|bife|asado|costillar|paleta|peceto|nalga|cuadril|lomo|milanesa vacuna)\b', 'Carnes'),
    (r'\b(cerdo|bondiola|carré|pechito de cerdo|costilla cerdo)\b', 'Carnes'),
    (r'\b(jamon|salchicha|mortadela|chorizo|salame|paleta cocida|fiambre)\b', 'Embutidos'),
    (r'\b(merluza|pescado|atún|sardina|calamar|langostino|caballa)\b', 'Pescados'),
    (r'\b(leche|yogur|queso|crema de leche|manteca|ricota)\b', 'Lacteos'),
    (r'\b(huevo|huevos)\b', 'Huevos'),
    (r'\b(arroz|fideos|pasta|spaghetti|tallarines|polenta|avena|maicena|galletita|cereal|pan lactal|pan de miga)\b', 'Cereales'),
    (r'\b(lenteja|garbanzo|poroto|arvejas|soja|haba)\b', 'Leguminosas'),
    (r'\b(papa|zapallo|calabaza|choclo|zanahoria|tomate|lechuga|cebolla|espinaca|acelga|brócoli|coliflor|berenjena|batata)\b', 'Hortalizas'),
    (r'\b(manzana|naranja|mandarina|banana|pera|durazno|damasco|uva|limón|pomelo|kiwi|frutilla)\b', 'Frutas'),
    (r'\b(aceite de girasol|aceite de maiz|aceite de oliva|aceite vegetal)\b', 'Aceites'),
    (r'\b(azúcar|azucar|miel|dulce de leche|mermelada)\b', 'Azucares'),
    (r'\b(almendra|nuez|maní|avellana|castaña|pistacho)\b', 'FrutosSecos'),
]

# Caché local
CACHE_DIR = Path(__file__).parent / "cache"
CACHE_DIR.mkdir(exist_ok=True)


def _mapear_grupo(descripcion: str) -> str | None:
    desc = str(descripcion).lower()
    for patron, grupo in KEYWORDS_GRUPO:
        if re.search(patron, desc, re.IGNORECASE):
            return grupo
    return None


def _url_del_dia() -> tuple[str, str]:
    """Retorna la URL y nombre del archivo SEPA para el día actual."""
    dia = datetime.now().weekday()  # 0=lunes, 6=domingo
    return SEPA_URLS[dia]


def _descargar_y_filtrar(url: str, nombre_dia: str) -> pd.DataFrame:
    """
    Descarga el ZIP de SEPA en streaming, extrae el CSV y filtra
    solo las filas de alimentos. Procesa ~12M filas → devuelve ~50K.
    """
    print(f"  ⬇ Descargando SEPA {nombre_dia} (puede tardar 1-2 minutos)...")

    headers = {
        'User-Agent': 'Mozilla/5.0 (compatible; NutriPlan/2.0; +https://nutriplan-oms.vercel.app)',
        'Accept': 'application/zip, application/octet-stream, */*',
        'Accept-Encoding': 'gzip, deflate',
    }

    with requests.get(url, stream=True, timeout=300, headers=headers) as resp:
        resp.raise_for_status()

        # Leer el ZIP completo en memoria (comprimido: ~200-400MB)
        contenido = io.BytesIO()
        descargados = 0
        for chunk in resp.iter_content(chunk_size=2 * 1024 * 1024):  # 2MB chunks
            contenido.write(chunk)
            descargados += len(chunk)
            if descargados % (50 * 1024 * 1024) == 0:  # cada 50MB
                print(f"    [SEPA] {descargados // (1024*1024)}MB descargados...")

        contenido.seek(0)

    print(f"  ✓ Descarga completa ({descargados // (1024*1024)}MB). Procesando...")

    # Abrir el ZIP y leer el CSV en chunks
    registros_alimentos = []

    with zipfile.ZipFile(contenido) as zf:
        # El ZIP contiene un CSV con el mismo nombre que el ZIP
        nombre_csv = zf.namelist()[0]
        print(f"  Archivo dentro del ZIP: {nombre_csv}")

        with zf.open(nombre_csv) as csv_file:
            # Leer en chunks para no cargar todo en memoria
            for chunk in pd.read_csv(
                csv_file,
                chunksize=50_000,
                usecols=lambda c: c in [
                    'id_comercio', 'id_bandera', 'id_sucursal',
                    'comercio_bandera_nombre', 'comercio_provincia',
                    'producto_descripcion', 'producto_precio_lista',
                ],
                dtype=str,
                encoding='utf-8',
                on_bad_lines='skip',
                low_memory=False,
            ):
                # Filtrar solo alimentos
                chunk['GRUPO'] = chunk['producto_descripcion'].apply(_mapear_grupo)
                alimentos = chunk.dropna(subset=['GRUPO'])
                if len(alimentos):
                    registros_alimentos.append(alimentos)

    if not registros_alimentos:
        raise RuntimeError("No se encontraron registros de alimentos en el CSV")

    df = pd.concat(registros_alimentos, ignore_index=True)
    print(f"  ✓ {len(df):,} registros de alimentos encontrados")
    return df


def _procesar_precios(df: pd.DataFrame,
                      provincia_codigo: str | None) -> pd.DataFrame:
    """
    Filtra por provincia (si se especificó) y calcula precio mediano por grupo.
    """
    # Filtrar por provincia
    if provincia_codigo and 'comercio_provincia' in df.columns:
        nombre_prov = PROVINCIAS.get(provincia_codigo, '').upper()
        if nombre_prov:
            mask = df['comercio_provincia'].str.upper().str.contains(
                nombre_prov[:6], na=False  # los primeros 6 caracteres son suficientes
            )
            df_prov = df[mask]
            n_prov = len(df_prov)
            if n_prov > 100:  # hay suficientes datos provinciales
                df = df_prov
                print(f"  ✓ {n_prov:,} registros para {PROVINCIAS[provincia_codigo]}")
            else:
                print(f"  ⚠ Pocos datos para la provincia, usando precios nacionales")

    df['precio'] = pd.to_numeric(df['producto_precio_lista'], errors='coerce')
    df = df.dropna(subset=['precio'])
    df = df[df['precio'] > 0]

    # Extraer gramos del nombre del producto para normalizar a /100g
    def extraer_gramos(desc):
        desc = str(desc).lower()
        for pat, mult in [(r'(\d+(?:[,\.]\d+)?)\s*kg', 1000),
                          (r'(\d+(?:[,\.]\d+)?)\s*gr?(?:\b|$)', 1)]:
            m = re.search(pat, desc)
            if m:
                val = float(m.group(1).replace(',', '.'))
                return val * mult if mult == 1000 else val
        return 1000  # default 1kg

    df['gramos'] = df['producto_descripcion'].apply(extraer_gramos)
    df['precio_100g'] = df['precio'] / df['gramos'] * 100

    # Eliminar outliers por grupo
    resultado = []
    for grupo, gdf in df.groupby('GRUPO'):
        p5  = gdf['precio_100g'].quantile(0.05)
        p95 = gdf['precio_100g'].quantile(0.95)
        flt = gdf[(gdf['precio_100g'] >= p5) & (gdf['precio_100g'] <= p95)]
        resultado.append({
            'GRUPO':             grupo,
            'PRECIO_MEDIANA_100G': round(flt['precio_100g'].median(), 2),
            'N_PRODUCTOS':       len(flt),
            'FUENTE':            'SEPA',
        })

    return pd.DataFrame(resultado)


def obtener_precios_sepa(
    provincia_codigo: str | None = None,
    provincia_nombre: str | None = None,
    forzar_descarga:  bool = False,
    max_age_horas:    int  = 24,
) -> pd.DataFrame:
    """
    Obtiene precios SEPA reales, con caché de 24 horas.
    Fallback automático a precios de referencia si falla la descarga.
    """
    # Resolver provincia
    if provincia_nombre and not provincia_codigo:
        for nombre_key, cod in PROVINCIAS_INV.items():
            if provincia_nombre.upper() in nombre_key:
                provincia_codigo = cod
                break

    cache_key = provincia_codigo or 'nacional'
    cache_file = CACHE_DIR / f"precios_{cache_key}.parquet"

    # Verificar caché válido
    if not forzar_descarga and cache_file.exists():
        edad = datetime.now() - datetime.fromtimestamp(cache_file.stat().st_mtime)
        if edad < timedelta(hours=max_age_horas):
            print(f"✓ Usando caché SEPA ({int(edad.total_seconds() // 3600)}h de antigüedad)")
            return pd.read_parquet(cache_file)

    # Descargar y procesar
    try:
        url, nombre_dia = _url_del_dia()
        df_raw    = _descargar_y_filtrar(url, nombre_dia)
        df_precios = _procesar_precios(df_raw, provincia_codigo)
        df_precios.to_parquet(cache_file)
        print(f"✓ Precios SEPA actualizados y guardados en caché")
        return df_precios
    except Exception as e:
        print(f"⚠ Error descargando SEPA: {e}")
        print("  Usando precios de referencia como fallback.")
        return _precios_referencia()


def _precios_referencia() -> pd.DataFrame:
    """Precios de referencia en ARS (estimados Mayo 2025). Fallback sin conexión."""
    REF = {
        'Cereales':     250,
        'Leguminosas':  200,
        'Hortalizas':   120,
        'Frutas':       180,
        'FrutosSecos': 1200,
        'Lacteos':      350,
        'Huevos':       280,
        'Aceites':      500,
        'Azucares':     200,
        'Pescados':     650,
        'Carnes':      1100,
        'Embutidos':   1200,
        'Aves':         600,
    }
    return pd.DataFrame([
        {'GRUPO': g, 'PRECIO_MEDIANA_100G': p,
         'N_PRODUCTOS': 0, 'FUENTE': 'referencia_local'}
        for g, p in REF.items()
    ])


def aplicar_precios(df_alimentos: pd.DataFrame,
                    df_precios: pd.DataFrame) -> pd.DataFrame:
    """Une precios SEPA con el DataFrame de alimentos por grupo."""
    precio_map = df_precios.set_index('GRUPO')['PRECIO_MEDIANA_100G'].to_dict()
    df = df_alimentos.copy()
    df['PRECIO_100G'] = df['GRUPO'].map(precio_map)
    df['PRECIO_g']    = df['PRECIO_100G'] / 100.0
    sin_precio = df['PRECIO_100G'].isna().sum()
    if sin_precio:
        print(f"⚠ {sin_precio} alimentos sin precio asignado")
    return df


if __name__ == '__main__':
    print("Test de precios de referencia:")
    df = _precios_referencia()
    print(df.to_string(index=False))
    print()
    print("URLs SEPA por día:")
    dias = ['Lunes','Martes','Miércoles','Jueves','Viernes','Sábado','Domingo']
    for i, (url, nombre) in SEPA_URLS.items():
        print(f"  {dias[i]}: {nombre}")


# ─── Caché en memoria para Railway (sin disco persistente) ───────────────────

import threading
import gc
from datetime import datetime

class _SepaCacheManager:
    """
    Gestiona la descarga y caché de precios SEPA en background.
    Thread-safe. Compatible con entornos sin disco persistente (Railway).
    """
    def __init__(self):
        self._lock    = threading.Lock()
        self._df      = None
        self._precios_por_provincia = {}
        self._status  = 'pendiente'
        self._mensaje = 'Precios SEPA: cargando...'
        self._actualizado = None
        self._intentos = 0
        self._MAX_INTENTOS = 3

    @property
    def status(self):
        with self._lock:
            return self._status

    @property
    def mensaje(self):
        with self._lock:
            return self._mensaje

    @property
    def listo(self):
        with self._lock:
            return self._status == 'listo' and self._df is not None

    def get_precios(self, provincia_codigo: str | None = None) -> pd.DataFrame:
        """
        Devuelve precios SEPA si están disponibles, o referencia mientras carga.
        """
        with self._lock:
            if self._status == 'listo' and self._df is not None:
                return self._df.copy()
        return _precios_referencia()

    def fuente(self) -> str:
        with self._lock:
            return 'SEPA' if self._status == 'listo' else 'referencia_local'

    def _cargar_desde_json(self):
        """
        Carga precios desde data/precios_sepa.json (generado por actualizar_precios_sepa.py).
        Este archivo vive en el repo y se despliega automáticamente con cada push.
        """
        json_path = Path(__file__).parent / 'precios_sepa.json'
        if not json_path.exists():
            raise FileNotFoundError(
                f"No se encontró {json_path}. "
                "Corré actualizar_precios_sepa.py en tu máquina local."
            )

        import json as _json
        datos = _json.loads(json_path.read_text(encoding='utf-8'))
        precios_nacionales = datos['precios'].get('nacional', {})

        if not precios_nacionales:
            raise ValueError("El JSON no tiene precios nacionales.")

        # Convertir a DataFrame compatible con aplicar_precios()
        rows = [
            {
                'GRUPO':               grupo,
                'PRECIO_MEDIANA_100G': precio,
                'N_PRODUCTOS':         1,
                'FUENTE':              'SEPA',
            }
            for grupo, precio in precios_nacionales.items()
        ]
        df = pd.DataFrame(rows)

        actualizado = datos.get('actualizado', 'desconocido')
        return df, actualizado, datos.get('precios', {})

    def get_precios(self, provincia_codigo: str | None = None) -> pd.DataFrame:
        """
        Devuelve precios SEPA si están disponibles, o referencia mientras carga.
        Si se especifica provincia, intenta devolver precios provinciales.
        """
        with self._lock:
            if self._status == 'listo' and self._df is not None:
                # Intentar devolver precios provinciales si están disponibles
                if provincia_codigo and self._precios_por_provincia:
                    prov_data = self._precios_por_provincia.get(provincia_codigo)
                    if prov_data:
                        rows = [
                            {'GRUPO': g, 'PRECIO_MEDIANA_100G': p,
                             'N_PRODUCTOS': 1, 'FUENTE': 'SEPA'}
                            for g, p in prov_data.items()
                        ]
                        return pd.DataFrame(rows)
                return self._df.copy()
        return _precios_referencia()

    def _descargar(self):
        """Carga precios desde JSON del repo. Se ejecuta en background."""
        with self._lock:
            self._status  = 'descargando'
            self._mensaje = 'Cargando precios SEPA...'
            self._intentos += 1
            self._precios_por_provincia = {}

        try:
            print(f"[SEPA] Cargando precios desde JSON (intento {self._intentos})")
            df_precios, actualizado, todos_precios = self._cargar_desde_json()

            with self._lock:
                self._df                   = df_precios
                self._precios_por_provincia = todos_precios
                self._status               = 'listo'
                self._actualizado          = actualizado
                self._mensaje              = f'Precios SEPA: {actualizado[:10]}'

            print(f"[SEPA] ✓ Precios cargados del {actualizado[:10]}")

        except Exception as e:
            msg = f'Error SEPA: {type(e).__name__}: {str(e)[:200]}'
            with self._lock:
                self._status  = 'error'
                self._mensaje = msg + '. Usando precios de referencia.'
            print(f"[SEPA] ⚠ {msg}")

    def iniciar(self, delay_segundos: int = 5):
        """
        Lanza la descarga en background. Llamar al startup del servidor.
        El delay evita que compita con la inicialización del servidor.
        """
        def _lanzar():
            import time
            time.sleep(delay_segundos)
            self._descargar()

        t = threading.Thread(target=_lanzar, daemon=True)
        t.name = 'sepa-downloader'
        t.start()
        print(f"[SEPA] Descarga programada en {delay_segundos}s")

    def programar_refresco(self, intervalo_horas: int = 24):
        """No necesario con JSON en repo — el refresco ocurre al hacer deploy."""


# Instancia global — se importa desde main.py
sepa_cache = _SepaCacheManager()
