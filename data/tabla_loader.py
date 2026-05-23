"""
tabla_loader.py
Carga y normaliza la tabla de composición de alimentos.

La tabla tiene estructura fija en todas las hojas:
  Fila 0: encabezados de sección (COMPOSICION, VITAMINAS, etc.)
  Fila 1: sub-encabezados (unidades)
  Fila 2: sub-encabezados (unidades)
  Fila 3: nombres de columnas (NRO, ALIMENTO, ESTADO, CAL, PR, ...)
  Fila 4+: datos

Columnas en posición (índice base 0):
  0: NRO     — número de ítem
  1: ALIMENTO
  2: ESTADO  — estado/preparación
  3: CAL     — Energía (kcal/100g)
  4: PR      — Proteínas (g/100g)
  5: GR      — Grasas totales (g/100g)
  6: HC      — Hidratos de carbono (g/100g)
  7: H2O     — Agua (g/100g)
  8: NE      — Nitrógeno equivalente
  9: VIT_A   — Vitamina A (UI/100g)
 10: VIT_B1  — Vitamina B1/Tiamina (mcg/100g)
 11: VIT_B2  — Vitamina B2/Riboflavina (mcg/100g)
 12: VIT_C   — Vitamina C (mcg/100g)
 13: NIAC    — Niacina (mcg/100g)
 14: NA      — Sodio (mg/100g)
 15: K       — Potasio (mg/100g)
 16: CA      — Calcio (mg/100g)
 17: MG      — Magnesio (mg/100g)
 18: FE      — Hierro (mg/100g)
 19: CU      — Cobre (mg/100g)
 20: P       — Fósforo (mg/100g)
 21: S       — Azufre (mg/100g)
 22: CL      — Cloro (mg/100g)
 23-30: Aminoácidos (Fen, Ileu, Leu, Lis, Met, Tre, Tri, Val) mg/100g
 31: ACID    — Acidez
 32: ALCAL   — Alcalinidad
"""

import pandas as pd
import numpy as np
import openpyxl
import re
from pathlib import Path

# ─── Mapeo de hojas a grupo lógico ────────────────────────────────────────────
HOJAS_GRUPOS = {
    'Cereales':                 'Cereales',
    'leguminosas':              'Leguminosas',
    'tuberculos y hortalizas':  'Hortalizas',
    'frutos frescos':           'Frutas',
    'frutos secos':             'FrutosSecos',
    'Leche y derivados':        'Lacteos',
    'Huevos':                   'Huevos',
    'Azucares y dulces varios': 'Azucares',
    'aceites y grasas':         'Aceites',
    'Pescados':                 'Pescados',
    'Carne':                    'Carnes',
    'Cerdo':                    'Carnes',
    'Cordero':                  'Carnes',
    'Oveja':                    'Carnes',
    'Ternera':                  'Carnes',
    'Vaca':                     'Carnes',
    'Embutidos':                'Embutidos',
    'Aves':                     'Aves',
    'Caza':                     'Carnes',
}

COL_NAMES = [
    'NRO', 'ALIMENTO', 'ESTADO',
    'CAL', 'PR', 'GR', 'HC', 'H2O', 'NE',
    'VIT_A', 'VIT_B1', 'VIT_B2', 'VIT_C', 'NIAC',
    'NA', 'K', 'CA', 'MG', 'FE', 'CU', 'P', 'S', 'CL',
    'FEN', 'ILEU', 'LEU', 'LIS', 'MET', 'TRE', 'TRI', 'VAL',
    'ACID', 'ALCAL'
]

NUTRIENT_COLS = [
    'CAL', 'PR', 'GR', 'HC',
    'VIT_A', 'VIT_B1', 'VIT_B2', 'VIT_C',
    'CA', 'FE', 'MG', 'K', 'P'
]

# ─── FIX 1: Reclasificaciones post-carga ──────────────────────────────────────
# Alimentos mal ubicados en la hoja de origen que deben ir a otro grupo lógico
RECLASIFICACIONES = {
    # (alimento_lower, estado_lower_o_None) → grupo_correcto
    ('soja', 'harina'):   'Leguminosas',
    ('soja', None):       'Leguminosas',    # cualquier estado de soja
}

# ─── FIX 2: Lista de exclusión de disponibilidad ──────────────────────────────
# Alimentos que existen en la tabla nutricional pero NO son de compra cotidiana
# en supermercados/almacenes argentinos, o son ingredientes industriales.
# Se marcan DISPONIBLE=False y el optimizador no los usa.
#
# Criterios de exclusión:
#   A) Fauna no disponible en comercios (caza exótica, ballena)
#   B) Ingredientes industriales (almidones puros, extractos)
#   C) Formas concentradas/deshidratadas de alimentos frescos básicos
#      (huevo seco, leche total seca — la leche en polvo descremada sí es común)
#   D) Productos no identificables en góndola argentina (majuela, achicoria tostada*)
#
# * achicoria tostada es sustituto de café — sí existe pero no es alimento base
EXCLUIR_ALIMENTOS = {
    # Fauna no disponible
    'ballena (carne)',
    'cobaya',
    'ciervo',
    'corzo',
    # Almidones / ingredientes industriales
    'amidon de arroz',
    'almidon de maiz',
    'almidon de trigo',
    'malta (extracto)',
    'polvo para flanes',
    # Forma concentrada de alimentos básicos (el LP los sobreusa por densidad)
    'huevo (seco)',          # reemplazado por Huevo entero / Clara / Yema crudos
    'leche de vaca (total seca)',   # muy concentrada; leche en polvo descremada sí OK
    'leche de burra',        # no disponible en AR
    # Frutas exóticas / no identificables en góndola
    'majuela',
    # Derivados industriales del aceite
    'aceite higado bacalao',    # suplemento, no aceite de cocina
    # Sustitutos de bebida / no alimentos base
    'achicoria (tostada)',      # sustituto del café, no vegetal de consumo masivo
                                # Además tiene dato de Fe (58 mg/100g) claramente erróneo
    # Legumbres no disponibles en Argentina o peligrosas en exceso
    'almortas',                 # Lathyrus sativus — no disponible en AR; causa latirismo
}

# Exclusión por estado (aplica a CUALQUIER alimento con ese estado)
EXCLUIR_ESTADOS = {
    'almidon',    # patata (almidón) → almidón puro, no papa común
    'diastasada',
}

# ─── Límites individuales por tipo de alimento ────────────────────────────────
# Algunos alimentos son válidos nutricionalmente pero no se consumen en grandes
# cantidades (condimentos, hierbas). El optimizador los sobreusa si no se limitan.
# Formato: {palabra_clave_en_nombre_lower: gramos_maximo_dia}
LIMITES_INDIVIDUALES = {
    'perejil':    25,    # condimento/hierba — máx ~1 atado pequeño
    'estragon':   10,
    'laurel':      5,
    'tomillo':    10,
    'oregano':    10,
    'albahaca':   15,
    'cebollino':  15,
    'eneldo':     10,
    'comino':      5,
    'canela':      5,
    'pimienta':    5,
    'mostaza':    30,    # condimento
    'ketchup':    50,
    'mahonesa':   30,    # también en Aceites — ya tiene max de grupo
}

# ─── FIX 3: Penalización de formas concentradas/secas ─────────────────────────
# Para alimentos que tienen versión fresca Y seca, el LP tiende a usar la seca
# porque tiene más nutrientes por gramo al mismo precio.
# Solución: multiplicar el precio de la versión seca por un factor de "reconstitución"
# que refleja que 1g de leche seca ≠ 1g de leche fresca para el consumidor.
# El factor es aproximadamente la relación de calorías seco/fresco.
FACTOR_PRECIO_ESTADO = {
    'seco':          3.5,   # deshidratados → caro en equivalente fresco
    'secos':         3.5,
    'seca':          3.5,
    'deshidratado':  3.5,
    'polvo':         4.0,   # leches en polvo, etc.
    'concentrada':   2.5,
    'concent.':      2.5,
    'condens':       2.5,   # condensada
}

# Estados que SÍ son la forma normal de venta (no penalizar)
ESTADOS_NORMALES_SECOS = {
    'frutos secos',  # almendras, nueces, etc. → su forma natural es seca
}

# Grupos donde el estado seco ES la forma habitual de compra
GRUPOS_SECOS_NORMALES = {'FrutosSecos'}


def _limpiar_valor(val):
    """Convierte un valor de celda a float, manejando anotaciones como '(1) 4,5'."""
    if val is None:
        return np.nan
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).strip()
    s = re.sub(r'^\(\d+\)\s*', '', s)
    s = s.replace(',', '.')
    try:
        return float(s)
    except ValueError:
        return np.nan


def _es_excluido(alimento: str, estado: str, grupo: str) -> bool:
    """Determina si un alimento debe marcarse como no disponible."""
    alimento_l = alimento.lower().strip()
    estado_l   = estado.lower().strip() if estado else ''

    # Exclusión por nombre completo (nombre + estado)
    nombre_completo = f"{alimento_l} ({estado_l})" if estado_l else alimento_l
    if nombre_completo in EXCLUIR_ALIMENTOS:
        return True
    if alimento_l in EXCLUIR_ALIMENTOS:
        return True

    # Exclusión por estado
    for estado_excl in EXCLUIR_ESTADOS:
        if estado_excl in estado_l:
            return True

    return False


def _factor_precio(estado: str, grupo: str) -> float:
    """Devuelve el multiplicador de precio para estados concentrados."""
    if grupo in GRUPOS_SECOS_NORMALES:
        return 1.0   # frutos secos → no penalizar
    estado_l = estado.lower().strip() if estado else ''
    for estado_key, factor in FACTOR_PRECIO_ESTADO.items():
        if estado_key in estado_l:
            return factor
    return 1.0


def _reclasificar(alimento: str, estado: str, grupo_actual: str) -> str:
    """Aplica correcciones de grupo a alimentos mal clasificados en el Excel."""
    alimento_l = alimento.lower().strip()
    estado_l   = estado.lower().strip() if estado else ''

    for (alim_key, est_key), grupo_correcto in RECLASIFICACIONES.items():
        if alim_key in alimento_l:
            if est_key is None or est_key in estado_l:
                return grupo_correcto
    return grupo_actual


def cargar_tabla(ruta_excel: str | Path) -> pd.DataFrame:
    """
    Carga todas las hojas del Excel y devuelve un DataFrame unificado.

    Returns
    -------
    pd.DataFrame con columnas:
        ALIMENTO, ESTADO, GRUPO, HOJA,
        todas las de NUTRIENT_COLS,
        columnas *_g (por gramo),
        NOMBRE_COMPLETO, DISPONIBLE, FACTOR_PRECIO,
        PRECIO_100G, PRECIO_g (inicialmente NaN)
    """
    ruta = Path(ruta_excel)
    wb = openpyxl.load_workbook(ruta, read_only=True, data_only=True)

    dfs = []

    for hoja, grupo_base in HOJAS_GRUPOS.items():
        if hoja not in wb.sheetnames:
            print(f"⚠ Hoja '{hoja}' no encontrada, saltando.")
            continue

        ws = wb[hoja]
        filas = list(ws.iter_rows(values_only=True))

        if len(filas) < 5:
            continue

        datos = filas[4:]   # primeras 4 filas son encabezados
        registros = []

        for fila in datos:
            if len(fila) < 2 or fila[1] is None:
                continue
            alimento = str(fila[1]).strip()
            if not alimento:
                continue
            # Saltar filas que son sub-títulos de sección
            if alimento.upper() in {
                'ALIMENTOS', 'CEREALES', 'LEGUMINOSAS', 'FRUTOS', 'FRESCOS',
                'SECOS', 'LECHE Y ', 'DERIVADOS', 'HUEVOS', 'AZUCARES',
                'DULCES VARIOS', 'ACEITES Y GRASAS', 'PESCADOS', 'CARNE',
                'CERDO', 'CORDERO', 'OVEJA', 'TERNERA', 'VACA', 'EMBUTIDOS',
                'AVES', 'CAZA', 'HORTALIZAS', 'TUBERCULOS',
            }:
                continue

            estado = str(fila[2]).strip() if fila[2] else ''
            fila_ext = list(fila) + [None] * (33 - len(fila))

            # FIX 1: reclasificación
            grupo = _reclasificar(alimento, estado, grupo_base)

            rec = {
                'NRO':      fila_ext[0],
                'ALIMENTO': alimento,
                'ESTADO':   estado,
                'GRUPO':    grupo,
                'HOJA':     hoja,
            }
            for i, col in enumerate(COL_NAMES[3:], start=3):
                rec[col] = _limpiar_valor(fila_ext[i])

            registros.append(rec)

        if registros:
            dfs.append(pd.DataFrame(registros))

    if not dfs:
        raise ValueError("No se pudo cargar ninguna hoja del Excel.")

    df = pd.concat(dfs, ignore_index=True)

    # ── Conversiones de unidades ──────────────────────────────────────────────
    df['VIT_B1'] = df['VIT_B1'] / 1000    # mcg → mg
    df['VIT_B2'] = df['VIT_B2'] / 1000    # mcg → mg
    df['VIT_C']  = df['VIT_C']  / 1000    # mcg → mg

    # ── FIX 2: columna DISPONIBLE ─────────────────────────────────────────────
    df['DISPONIBLE'] = df.apply(
        lambda r: not _es_excluido(r['ALIMENTO'], r['ESTADO'], r['GRUPO']),
        axis=1
    )

    # ── Exclusión de ingredientes crudos no comestibles directamente ──────────
    def _es_materia_prima(alimento: str, estado: str) -> bool:
        alimento_l = alimento.lower().strip()
        estado_l   = estado.lower().strip() if estado else ''
        # Por estado
        for est_excl in EXCLUIR_ESTADOS_CRUDOS:
            if est_excl in estado_l:
                return True
        # Por nombre completo
        nombre_completo = f"{alimento_l} ({estado_l})" if estado_l else alimento_l
        if nombre_completo in EXCLUIR_MATERIAS_PRIMAS:
            return True
        if alimento_l in EXCLUIR_MATERIAS_PRIMAS:
            return True
        return False

    mask_mp = df.apply(
        lambda r: _es_materia_prima(r['ALIMENTO'], r['ESTADO']), axis=1
    )
    df.loc[mask_mp, 'DISPONIBLE'] = False

    # ── FIX 3: factor de precio para formas concentradas ─────────────────────
    df['FACTOR_PRECIO'] = df.apply(
        lambda r: _factor_precio(r['ESTADO'], r['GRUPO']),
        axis=1
    )

    # ── Columnas por gramo (para el LP) ──────────────────────────────────────
    for col in NUTRIENT_COLS:
        df[f'{col}_g'] = df[col] / 100.0

    # ── FIBRA estimada (g/100g) ───────────────────────────────────────────────
    def _estimar_fibra(alimento: str, grupo: str) -> float:
        alimento_l = alimento.lower().strip()
        # Buscar override específico por nombre
        for keyword, fibra in FIBRA_ESTIMADA_ALIMENTO.items():
            if keyword in alimento_l:
                return fibra
        # Fallback al promedio del grupo
        return FIBRA_ESTIMADA_GRUPO.get(grupo, 0.0)

    df['FIBRA']   = df.apply(lambda r: _estimar_fibra(r['ALIMENTO'], r['GRUPO']), axis=1)
    df['FIBRA_g'] = df['FIBRA'] / 100.0

    # ── Nombre completo del alimento ──────────────────────────────────────────
    df['NOMBRE_COMPLETO'] = df.apply(
        lambda r: f"{r['ALIMENTO']} ({r['ESTADO']})" if r['ESTADO'] else r['ALIMENTO'],
        axis=1
    )

    # ── Precio (se completa desde sepa_client.aplicar_precios) ───────────────
    df['PRECIO_100G'] = np.nan
    df['PRECIO_g']    = np.nan

    n_excluidos = (~df['DISPONIBLE']).sum()
    n_total = len(df)
    print(f"✓ Tabla cargada: {n_total} alimentos en {df['GRUPO'].nunique()} grupos")
    print(f"  ({n_total - n_excluidos} disponibles para optimización, "
          f"{n_excluidos} excluidos por baja disponibilidad)")

    return df


def resumen_tabla(df: pd.DataFrame) -> None:
    """Imprime un resumen de la tabla cargada."""
    print("\n── Alimentos por grupo (disponibles / total) ────────────────────")
    for grupo, sub in df.groupby('GRUPO'):
        total = len(sub)
        disp  = sub['DISPONIBLE'].sum()
        print(f"  {grupo:<20} {disp:>4} / {total:>4}")
    print(f"  {'TOTAL':<20} {df['DISPONIBLE'].sum():>4} / {len(df):>4}")

    print("\n── Completitud de nutrientes (% filas disponibles con dato) ────")
    df_disp = df[df['DISPONIBLE']]
    for col in NUTRIENT_COLS:
        pct = df_disp[col].notna().mean() * 100
        bar = '█' * int(pct / 5)
        print(f"  {col:<8} {pct:5.1f}% {bar}")

    print(f"\n── Alimentos excluidos ──────────────────────────────────────────")
    excl = df[~df['DISPONIBLE']][['ALIMENTO', 'ESTADO', 'GRUPO']].sort_values('GRUPO')
    print(excl.to_string(index=False))

    print(f"\n── Alimentos con penalización de precio (forma concentrada) ─────")
    pen = df[df['FACTOR_PRECIO'] > 1.0][['NOMBRE_COMPLETO', 'GRUPO', 'FACTOR_PRECIO']]
    print(pen.to_string(index=False))


if __name__ == '__main__':
    df = cargar_tabla('tabla_composicion_alimentos.xlsx')
    resumen_tabla(df)

# ─── Exclusión de ingredientes que requieren procesamiento ────────────────────
# Estos alimentos existen en la tabla pero no se consumen directamente —
# son materias primas que necesitan cocción o elaboración industrial.
# El LP los sobreusa porque son baratos y calóricamente densos.
EXCLUIR_ESTADOS_CRUDOS = {
    'grano',      # trigo grano, cebada grano, centeno grano
}

# Alimentos específicos que son materias primas no consumibles directamente
EXCLUIR_MATERIAS_PRIMAS = {
    'harina de trigo',
    'harina de maiz',
    'harina de centeno',
    'harina de cebada',
    'harina de avena',
    'salvado de trigo',
    'salvado de avena',
    'germen de trigo',
    'trigo (grano)',
    'cebada (grano)',
    'centeno (grano)',
    'maiz (grano)',
    'avena (grano)',
}

# ─── Fibra estimada por grupo (g/100g) ───────────────────────────────────────
# La tabla original no tiene fibra. Usamos estimaciones por grupo basadas
# en valores promedio de la base USDA FoodData Central.
# Es una aproximación conservadora — mejor que nada para el LP.
FIBRA_ESTIMADA_GRUPO = {
    'Cereales':    3.5,   # varía mucho: arroz blanco 0.4g, avena 10g → promedio
    'Leguminosas': 8.0,   # lentejas, porotos: 6-10g
    'Hortalizas':  2.5,   # promedio verduras frescas
    'Frutas':      2.0,   # promedio frutas frescas
    'FrutosSecos': 6.5,   # almendras, nueces: 5-8g
    'Lacteos':     0.0,
    'Huevos':      0.0,
    'Aceites':     0.0,
    'Azucares':    0.2,
    'Pescados':    0.0,
    'Carnes':      0.0,
    'Embutidos':   0.0,
    'Aves':        0.0,
}

# Ajuste por alimento específico (override del grupo)
FIBRA_ESTIMADA_ALIMENTO = {
    'arroz':        0.4,
    'arroz blanco': 0.4,
    'papa':         2.2,
    'patata':       2.2,
    'zapallo':      1.0,
    'calabaza':     1.0,
    'banana':       2.6,
    'manzana':      2.4,
    'naranja':      2.4,
    'zanahoria':    2.8,
    'espinaca':     2.2,
    'acelga':       1.6,
    'lechuga':      1.3,
    'tomate':       1.2,
    'lenteja':      8.0,
    'poroto':       7.5,
    'garbanzo':     7.6,
    'soja':         6.0,
    'pan':          2.7,
    'pan integral': 6.0,
    'avena':        10.0,
    'chocolate':    3.4,
    'almendra':     12.5,
    'mani':         8.5,
    'nuez':         6.7,
}
