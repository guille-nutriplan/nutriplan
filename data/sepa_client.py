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
    Descarga el ZIP de SEPA y procesa los CSV internos.
    Estructura: ZIP externo → ZIPs por cadena → productos.csv + sucursales.csv
    Separador: pipe |
    """
    print(f"  ⬇ Descargando SEPA {nombre_dia}...")

    headers = {'User-Agent': 'Mozilla/5.0 NutriPlan/2.0'}
    with requests.get(url, stream=True, timeout=300, headers=headers) as resp:
        resp.raise_for_status()
        contenido = io.BytesIO()
        descargados = 0
        for chunk in resp.iter_content(2 * 1024 * 1024):
            contenido.write(chunk)
            descargados += len(chunk)
            if descargados % (50 * 1024 * 1024) == 0:
                print(f"    {descargados // (1024*1024)}MB...")
        contenido.seek(0)

    print(f"  ✓ {descargados // (1024*1024)}MB descargados. Procesando...")

    # Acumuladores por cadena
    todos_productos  = []
    todas_sucursales = []
    cadenas_ok = 0

    with zipfile.ZipFile(contenido) as zf_outer:
        inner_zips = [e for e in zf_outer.namelist()
                      if e.lower().endswith('.zip') and not e.endswith('/')]
        print(f"  {len(inner_zips)} cadenas en el ZIP")

        for entry in inner_zips:
            try:
                with zf_outer.open(entry) as f:
                    inner_bytes = io.BytesIO(f.read())

                with zipfile.ZipFile(inner_bytes) as zf_inner:
                    archivos = {
                        Path(n).name.lower(): n
                        for n in zf_inner.namelist()
                        if not n.endswith('/')
                    }

                    # Leer sucursales.csv → id_sucursal + sucursales_provincia
                    suc_key = next(
                        (k for k in archivos if 'sucursal' in k and k.endswith('.csv')),
                        None
                    )
                    df_suc = None
                    if suc_key:
                        with zf_inner.open(archivos[suc_key]) as f:
                            try:
                                df_suc = pd.read_csv(
                                    f, sep='|', dtype=str,
                                    encoding='utf-8', on_bad_lines='skip',
                                    usecols=lambda c: c in [
                                        'id_comercio', 'id_bandera', 'id_sucursal',
                                        'sucursales_provincia', 'sucursales_localidad',
                                    ]
                                )
                            except Exception:
                                pass

                    # Leer productos.csv → precios
                    prod_key = next(
                        (k for k in archivos if 'producto' in k and k.endswith('.csv')),
                        None
                    )
                    if prod_key and df_suc is not None:
                        with zf_inner.open(archivos[prod_key]) as f:
                            try:
                                df_prod = pd.read_csv(
                                    f, sep='|', dtype=str,
                                    encoding='utf-8', on_bad_lines='skip',
                                    usecols=lambda c: c in [
                                        'id_comercio', 'id_bandera', 'id_sucursal',
                                        'productos_descripcion',
                                        'productos_precio_lista',
                                        'productos_precio_referencia',
                                        'productos_cantidad_referencia',
                                        'productos_unidad_medida_referencia',
                                        'productos_cantidad_presentacion',
                                        'productos_unidad_medida_presentacion',
                                    ]
                                )
                                todos_productos.append(df_prod)
                                todas_sucursales.append(df_suc)
                                cadenas_ok += 1
                            except Exception:
                                pass

            except Exception as e:
                pass

    if not todos_productos:
        raise RuntimeError("No se pudo leer ninguna cadena del ZIP")

    print(f"  ✓ {cadenas_ok} cadenas procesadas")

    df_prod = pd.concat(todos_productos, ignore_index=True)
    df_suc  = pd.concat(todas_sucursales, ignore_index=True)

    # JOIN productos + sucursales por id_sucursal
    # (combinando id_comercio + id_bandera + id_sucursal como clave compuesta)
    for df in [df_prod, df_suc]:
        df['_key'] = (
            df.get('id_comercio', '').fillna('') + '_' +
            df.get('id_bandera', '').fillna('') + '_' +
            df['id_sucursal'].fillna('')
        )

    suc_map = df_suc[['_key', 'sucursales_provincia']].drop_duplicates('_key')
    df_prod = df_prod.merge(suc_map, on='_key', how='left')

    # Filtrar alimentos por descripción
    df_prod['GRUPO'] = df_prod['productos_descripcion'].apply(_mapear_grupo)
    df_alim = df_prod.dropna(subset=['GRUPO']).copy()

    if len(df_alim) == 0:
        raise RuntimeError("No se encontraron registros de alimentos")

    print(f"  ✓ {len(df_alim):,} registros de alimentos")

    # Renombrar para compatibilidad con _procesar_precios
    df_alim = df_alim.rename(columns={
        'sucursales_provincia':               'comercio_provincia',
        'productos_descripcion':              'producto_descripcion',
        'productos_precio_lista':             'producto_precio_lista',
        'productos_precio_referencia':        'precio_ref',
        'productos_unidad_medida_referencia': 'unidad_ref',
        'productos_cantidad_referencia':      'cantidad_ref',
    })

    if 'comercio_provincia' in df_alim.columns:
        provs = df_alim['comercio_provincia'].dropna().unique()[:6]
        print(f"  Provincias: {list(provs)}")

    return df_alim


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

    # Usar precio_referencia si está disponible (ya normalizado a kg/l)
    # Si no, calcular desde precio_lista dividiendo por gramos del nombre
    if 'precio_ref' in df.columns and 'unidad_ref' in df.columns:
        df['precio_ref_n'] = pd.to_numeric(df['precio_ref'], errors='coerce')
        df['cantidad_ref_n'] = pd.to_numeric(df.get('cantidad_ref', pd.Series(1, index=df.index)), errors='coerce').fillna(1)

        def _precio_100g_ref(row):
            p = row.get('precio_ref_n')
            u = str(row.get('unidad_ref', '')).lower().strip()
            c = row.get('cantidad_ref_n', 1) or 1
            if pd.isna(p) or p <= 0:
                return None
            precio_por_unidad = p / c
            if u in ('kg',):     return precio_por_unidad / 10   # /kg → /100g
            if u in ('g', 'gr'): return precio_por_unidad * 100  # /g  → /100g
            if u in ('l',):      return precio_por_unidad / 10   # /l  → /100ml
            if u in ('ml',):     return precio_por_unidad * 100  # /ml → /100ml
            return precio_por_unidad / 10  # asumir kg por defecto

        df['precio_100g'] = df.apply(_precio_100g_ref, axis=1)
        df = df.dropna(subset=['precio_100g'])
    else:
        df['precio'] = pd.to_numeric(df['producto_precio_lista'], errors='coerce')
        df = df.dropna(subset=['precio'])
        df = df[df['precio'] > 0]

        def extraer_gramos(desc):
            desc = str(desc).lower()
            for pat, mult in [(r'(\d+(?:[,\.]\d+)?)\s*kg', 1000),
                              (r'(\d+(?:[,\.]\d+)?)\s*gr?(?:\b|$)', 1)]:
                m = re.search(pat, desc)
                if m:
                    val = float(m.group(1).replace(',', '.'))
                    return val * mult if mult == 1000 else val
            return 1000

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
                    df_precios: pd.DataFrame,
                    precios_especificos: dict = None) -> pd.DataFrame:
    """
    Une precios SEPA con el DataFrame de alimentos.
    Primero aplica precio por grupo (mediana SEPA), luego sobreescribe
    con precios específicos por producto cuando están disponibles.
    """
    precio_map = df_precios.set_index('GRUPO')['PRECIO_MEDIANA_100G'].to_dict()
    df = df_alimentos.copy()
    df['PRECIO_100G'] = df['GRUPO'].map(precio_map)
    df['PRECIO_g']    = df['PRECIO_100G'] / 100.0

    # Aplicar precios específicos por alimento
    if precios_especificos:
        n_especificos = 0
        for _, row in df.iterrows():
            alimento = str(row.get('ALIMENTO', ''))
            # Buscar en el mapping alimento → keyword
            keyword = ALIMENTO_A_KEYWORD.get(alimento)
            if not keyword:
                # Intentar match parcial por nombre
                alimento_l = alimento.lower()
                for al_key, kw in ALIMENTO_A_KEYWORD.items():
                    if al_key.lower() in alimento_l or alimento_l in al_key.lower():
                        keyword = kw
                        break
            if keyword and keyword in precios_especificos:
                precio_esp = precios_especificos[keyword]
                df.loc[df['ALIMENTO'] == alimento, 'PRECIO_g'] = precio_esp / 100.0
                df.loc[df['ALIMENTO'] == alimento, 'PRECIO_100G'] = precio_esp
                n_especificos += 1

        if n_especificos:
            print(f"  ✓ Precios específicos aplicados: {n_especificos} alimentos")

    sin_precio = df['PRECIO_100G'].isna().sum()
    if sin_precio:
        df['PRECIO_g'] = df['PRECIO_g'].fillna(0)
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
        self._precios_especificos   = {}
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

    def get_precios_especificos(self) -> dict:
        """Devuelve precios específicos por keyword si están disponibles."""""
        with self._lock:
            return self._precios_especificos.copy() if self._precios_especificos else {}

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
        precios_esp = datos.get('precios_especificos', {})
        return df, actualizado, datos.get('precios', {}), precios_esp

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
            df_precios, actualizado, todos_precios, precios_esp = self._cargar_desde_json()

            with self._lock:
                self._df                   = df_precios
                self._precios_por_provincia = todos_precios
                self._precios_especificos   = precios_esp
                self._status               = 'listo'
                self._actualizado          = actualizado
                n_esp = len(precios_esp)
                self._mensaje              = f'Precios SEPA: {actualizado[:10]} ({n_esp} productos específicos)'

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



# ─── Keywords para extraer precios específicos de SEPA ────────────────────────
# Mapeamos conceptos nutricionales a términos que aparecen en productos SEPA
# Orden: más específico primero (se usa el primero que matchea)
KEYWORDS_PRECIO_ESPECIFICO = {
    # ── Cereales ──────────────────────────────────────────────────────────────
    'arroz integral':    ['arroz integral'],
    'arroz yamani':      ['arroz yamani', 'arroz yamaní'],
    'arroz':             ['arroz largo', 'arroz blanco', 'arroz parboil', 'arroz'],
    'fideos':            ['fideos spaghetti', 'fideos tallarín', 'fideos moño',
                          'fideos codo', 'fideos', 'pasta seca'],
    'polenta':           ['polenta'],
    'avena':             ['avena arrollada', 'avena'],
    'harina':            ['harina 0000', 'harina 000', 'harina de trigo'],
    'pan lactal':        ['pan lactal', 'pan de molde', 'pan americano'],
    'pan':               ['pan de mesa', 'pan blanco', 'pan francés'],
    'galletitas':        ['galletitas', 'galletas'],
    # ── Carnes vacunas ────────────────────────────────────────────────────────
    'carne picada':      ['carne picada', 'picada común', 'picada especial'],
    'asado de costilla': ['asado de tira', 'tira de asado', 'asado tira'],
    'nalga':             ['nalga'],
    'bola de lomo':      ['bola de lomo'],
    'paleta':            ['paleta vacuna', 'paleta'],
    'matambre':          ['matambre'],
    'osobuco':           ['osobuco', 'osso buco'],
    'cuadrada':          ['cuadrada'],
    # ── Aves ──────────────────────────────────────────────────────────────────
    'pechuga de pollo':  ['pechuga de pollo', 'pechuga pollo'],
    'muslo de pollo':    ['muslo pollo', 'pata muslo', 'cuarto trasero'],
    'pollo':             ['pollo entero', 'pollo trozado', 'pollo'],
    # ── Pescados ──────────────────────────────────────────────────────────────
    'atun':              ['atún en aceite', 'atún al natural', 'atun'],
    'merluza':           ['merluza', 'filet de merluza'],
    'sardinas':          ['sardinas'],
    # ── Lácteos ───────────────────────────────────────────────────────────────
    'leche':             ['leche entera', 'leche sachet', 'leche parcialmente descremada'],
    'leche descremada':  ['leche descremada'],
    'yogur':             ['yogur entero', 'yogur bebible', 'yogurt'],
    'queso':             ['queso cremoso', 'queso tybo', 'queso cuartirolo',
                          'queso pategrás', 'queso barra'],
    'manteca':           ['manteca'],
    'crema de leche':    ['crema de leche'],
    'ricota':            ['ricota'],
    # ── Huevos ────────────────────────────────────────────────────────────────
    'huevos':            ['huevos blancos', 'huevos colorados', 'huevo'],
    # ── Aceites ───────────────────────────────────────────────────────────────
    'aceite de oliva':   ['aceite de oliva', 'aceite oliva'],
    'aceite de girasol': ['aceite de girasol', 'aceite girasol'],
    'aceite de maiz':    ['aceite de maíz', 'aceite maiz', 'aceite de maiz'],
    'aceite de soja':    ['aceite de soja', 'aceite soja'],
    # ── Frutas ────────────────────────────────────────────────────────────────
    'banana':            ['banana'],
    'manzana':           ['manzana'],
    'naranja':           ['naranja'],
    'mandarina':         ['mandarina'],
    'pera':              ['pera'],
    'durazno':           ['durazno'],
    'uva':               ['uva'],
    'limon':             ['limón', 'limon'],
    'palta':             ['palta', 'aguacate'],
    # ── Verduras ──────────────────────────────────────────────────────────────
    'papa':              ['papa', 'papas'],
    'tomate':            ['tomate'],
    'cebolla':           ['cebolla'],
    'zanahoria':         ['zanahoria'],
    'lechuga':           ['lechuga'],
    'zapallo':           ['zapallo'],
    'calabaza':          ['calabaza', 'zapallo'],
    'morron':            ['morrón', 'morron', 'pimiento'],
    # ── Legumbres ─────────────────────────────────────────────────────────────
    'lentejas':          ['lentejas'],
    'garbanzos':         ['garbanzo', 'garbanzos'],
    'porotos':           ['porotos', 'poroto'],
    'arvejas':           ['arvejas', 'arveja'],
    # ── Azúcares ──────────────────────────────────────────────────────────────
    'azucar':            ['azúcar blanca', 'azucar refinada', 'azúcar'],
    'miel':              ['miel de abeja', 'miel'],
    # ── Frutos secos ──────────────────────────────────────────────────────────
    'mani':              ['maní', 'mani tostado', 'mani'],
    'almendras':         ['almendras'],
    'nueces':            ['nueces'],
}

# Mapping de nombre de alimento → keyword SEPA
# Permite asignar un precio específico a cada alimento de la tabla
ALIMENTO_A_KEYWORD = {
    # Cereales
    'Arroz':                'arroz',
    'Arroz integral':       'arroz integral',
    'Arroz yamaní':         'arroz yamani',
    'Fideos':               'fideos',
    'Macarrones':           'fideos',
    'Polenta':              'polenta',
    'Avena':                'avena',
    'Harina de trigo':      'harina',
    'Pan':                  'pan',
    # Carnes vacunas
    'Carne picada':         'carne picada',
    'Asado de costilla':    'asado de costilla',
    'Nalga':                'nalga',
    'Bola de lomo':         'bola de lomo',
    'Paleta':               'paleta',
    'Matambre':             'matambre',
    'Osobuco':              'osobuco',
    'Cuadrada':             'cuadrada',
    'Bistec':               'carne picada',
    'Carne':                'carne picada',
    # Aves
    'Pollo':                'pollo',
    'Pechuga de pollo':     'pechuga de pollo',
    'Muslo de pollo':       'muslo de pollo',
    # Pescados
    'Atun':                 'atun',
    'Abadejo':              'merluza',
    'Merluza':              'merluza',
    'Salmon':               'merluza',
    # Lácteos
    'Leche de vaca':        'leche',
    'Leche':                'leche',
    'Yogur':                'yogur',
    'Queso':                'queso',
    'Manteca':              'manteca',
    'Crema':                'crema de leche',
    'Ricota':               'ricota',
    # Huevos
    'Huevo':                'huevos',
    # Aceites
    'Aceite de oliva':      'aceite de oliva',
    'Aceite de girasol':    'aceite de girasol',
    'Aceite de maíz':       'aceite de maiz',
    'Aceite de soja':       'aceite de soja',
    'Mayonesa':             'aceite de girasol',
    # Frutas
    'Platano':              'banana',
    'Pera':                 'pera',
    'Manzana':              'manzana',
    'Naranja':              'naranja',
    'Mandarina':            'mandarina',
    'Durazno':              'durazno',
    'Palta':                'palta',
    # Verduras
    'Patata':               'papa',
    'Tomate':               'tomate',
    'Cebolla':              'cebolla',
    'Zanahoria':            'zanahoria',
    'Lechuga':              'lechuga',
    'Zapallo':              'zapallo',
    'Zapallo cabutia':      'zapallo',
    'Zapallo anquito':      'zapallo',
    'Morron':               'morron',
    'Morrón rojo':          'morron',
    'Morrón verde':         'morron',
    # Legumbres
    'Lentejas':             'lentejas',
    'Garbanzos':            'garbanzos',
    'Judias blancas':       'porotos',
    'Judias rojas':         'porotos',
    'Arvejas':              'arvejas',
    # Azúcares
    'Azucar':               'azucar',
    'Miel de Abeja':        'miel',
    # Frutos secos
    'Mani':                 'mani',
    'Almendra':             'almendras',
    'Nuez':                 'nueces',
}


def _extraer_precios_especificos(df_raw: pd.DataFrame) -> dict:
    """
    Extrae precios medianos por producto específico desde el DataFrame crudo de SEPA.
    Busca en `producto_descripcion` con los keywords definidos.
    Retorna dict: {keyword: precio_por_100g}
    """
    resultados = {}
    desc_col = 'producto_descripcion'
    if desc_col not in df_raw.columns:
        return resultados

    df = df_raw.copy()
    df['precio_100g_num'] = pd.to_numeric(df.get('precio_ref'), errors='coerce')

    # Normalizar unidad a /100g
    def _normalizar(row):
        p = row.get('precio_100g_num')
        u = str(row.get('unidad_ref', '')).lower().strip()
        c = float(row.get('cantidad_ref', 1) or 1)
        if pd.isna(p) or p <= 0:
            return None
        precio_por_unidad = p / c
        if u == 'kg':  return precio_por_unidad / 10
        if u in ('g','gr'): return precio_por_unidad * 100
        if u == 'l':   return precio_por_unidad / 10
        if u == 'ml':  return precio_por_unidad * 100
        return precio_por_unidad / 10  # default kg

    df['precio_100g_norm'] = df.apply(_normalizar, axis=1)
    df = df.dropna(subset=['precio_100g_norm'])
    df = df[df['precio_100g_norm'] > 0]
    df = df[df['precio_100g_norm'] < 5000]  # eliminar outliers

    desc_lower = df[desc_col].fillna('').str.lower()

    for keyword, terminos in KEYWORDS_PRECIO_ESPECIFICO.items():
        mask = pd.Series(False, index=df.index)
        for termino in terminos:
            mask |= desc_lower.str.contains(termino.lower(), na=False)

        matches = df[mask]['precio_100g_norm']
        if len(matches) >= 5:  # mínimo 5 productos para que sea representativo
            precio = round(float(matches.median()), 2)
            resultados[keyword] = precio
            print(f"    {keyword:<25} ${precio:>8.2f}/100g  ({len(matches)} productos)")

    return resultados



# Instancia global — se importa desde main.py
sepa_cache = _SepaCacheManager()
