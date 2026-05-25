"""
optimizer.py
Motor de optimización de dieta por programación lineal.

Resuelve:
  min  Σ precio_g[i] · x[i]
  s.t.
    Nutrientes mínimos/máximos OMS
    Grupos mínimos/máximos
    Peso total mínimo/máximo (saciedad)
    Fibra mínima (OMS)
    0 <= x[i] <= max_por_alimento[i]
"""

import numpy as np
import pandas as pd
from scipy.optimize import linprog
from dataclasses import dataclass, field
from typing import Optional


# ─── Parámetros del optimizador ───────────────────────────────────────────────

MAX_POR_GRUPO = {
    'Cereales':    400,
    'Leguminosas': 200,
    'Hortalizas':  500,
    'Frutas':      400,
    'FrutosSecos': 100,
    'Lacteos':     500,
    'Huevos':      150,
    'Aceites':      60,
    'Azucares':    100,
    'Pescados':    300,
    'Carnes':      250,
    'Embutidos':   100,
    'Aves':        300,
}

MIN_POR_GRUPO = {
    'Cereales':    50,
    'Leguminosas': 20,
    'Hortalizas':  80,
    'Frutas':      50,
    'FrutosSecos':  0,
    'Lacteos':     50,
    'Huevos':       0,
    'Aceites':     10,
    'Azucares':     0,
    'Pescados':     0,
    'Carnes':       0,
    'Embutidos':    0,
    'Aves':         0,
}

MIN_PROTEINA_ANIMAL = 30

# ─── FIX: Peso total diario (saciedad) ───────────────────────────────────────
# Una dieta adulta realista tiene entre 1.200 y 2.800g de alimentos sólidos/día.
# Esto fuerza al LP a elegir mayor volumen y variedad de alimentos.
MIN_GRAMOS_TOTAL = 1200   # g/día — mínimo para saciedad real
MAX_GRAMOS_TOTAL = 2800   # g/día — evita soluciones con cantidades absurdas

# Ajuste por rango etario (factor sobre el default adulto)
FACTOR_PESO_ETARIO = {
    '0-6m':        0.25,
    '6-12m':       0.35,
    '1-3':         0.50,
    '4-6':         0.60,
    '7-9':         0.70,
    '10-13M':      0.80,
    '10-13F':      0.78,
    '14-17M':      0.95,
    '14-17F':      0.82,
    '18-29M':      1.00,
    '18-29F':      0.85,
    '30-59M':      1.00,
    '30-59F':      0.85,
    '60+M':        0.90,
    '60+F':        0.80,
    'embarazada':  0.95,
    'lactante_madre': 1.05,
}


@dataclass
class FiltrosDieta:
    celiaco:     bool = False
    sin_lactosa: bool = False
    vegetariano: bool = False
    vegano:      bool = False
    alergenos:   list[str] = field(default_factory=list)

    def __post_init__(self):
        if self.vegano:
            self.vegetariano = True
            self.sin_lactosa = True


@dataclass
class ResultadoOptimizacion:
    exito:          bool
    mensaje:        str
    alimentos:      pd.DataFrame
    aportes:        dict
    costo_total:    float
    costo_mensual:  float
    fuente_precios: str
    req:            dict
    infactibilidad_detalle: Optional[str] = None



# ─── Restricciones etarias — Guías Alimentarias Ministerio Salud Nación ──────
# Fuente: "Guías Alimentarias para la Población Infantil" — MSyA Nación, 2006

RESTRICCIONES_ETARIAS = {
    "0-6m": {
        "solo_leche": True,
        "mensaje": (
            "Lactantes 0-6 meses: SOLO leche materna o formula infantil. "
            "No se necesita ningun alimento solido, ni agua, ni infusiones. "
            "La leche materna exclusiva cubre todas las necesidades en esta etapa."
        ),
    },
    "6-12m": {
        "solo_leche": False,
        "grupos_excluidos": ["Embutidos", "FrutosSecos"],
        "keywords_excluidos": ["miel", "soja", "copetin", "salchich", "fiambre", "hamburguesa"],
        "max_por_grupo": {
            "Azucares": 15,
            "Aceites":  10,
            "Pescados": 50,
        },
        "aviso": (
            "Plan complementario 6-12 meses (Guias MSN Argentina). "
            "Continuar con leche materna o formula como base. "
            "Sin sal agregada, sin miel, sin soja, sin frutos secos enteros. "
            "Legumbres: tamizadas y sin piel desde los 7-8 meses."
        ),
    },
    "1-3": {
        "solo_leche": False,
        "grupos_excluidos": ["Embutidos"],
        "keywords_excluidos": ["copetin", "fiambre", "salchich", "hamburguesa"],
        "max_por_grupo": {
            "FrutosSecos": 0,
            "Azucares":   30,
        },
        "aviso": (
            "Plan 1-3 anos (Guias MSN Argentina). "
            "Alimentos blandos o en trozos pequenos. "
            "Sin frutos secos enteros (riesgo atragantamiento). "
            "Sin fiambres ni embutidos industriales."
        ),
    },
}


def _aplicar_filtros(df: pd.DataFrame, filtros: FiltrosDieta) -> pd.DataFrame:
    df = df.copy()

    if filtros.celiaco:
        mask_ingr = df['ALIMENTO'].str.lower().str.contains(
            r'trigo|cebada|centeno|avena|sémola|semola', na=False, regex=True)
        mask_gluten_cereales = (
            (df['GRUPO'] == 'Cereales') &
            df['ALIMENTO'].str.lower().str.contains(
                r'macarr|fideos|pasta|galleta|buñuelo|semola|sémola'
                r'|pan de trigo|pan de avena|pan de cebada|pan de centeno'
                r'|pan de viena|tarta',
                na=False, regex=True
            )
        )
        mask_pan_gluten = (
            (df['GRUPO'] == 'Cereales') &
            df['ALIMENTO'].str.lower().str.contains(r'\bpan\b', na=False, regex=True) &
            ~df['ALIMENTO'].str.lower().str.contains(r'maiz|arroz', na=False, regex=True)
        )
        df = df[~(mask_ingr | mask_gluten_cereales | mask_pan_gluten)]

    if filtros.sin_lactosa:
        df = df[df['GRUPO'] != 'Lacteos']

    if filtros.vegetariano:
        df = df[~df['GRUPO'].isin(['Carnes', 'Aves', 'Pescados', 'Embutidos'])]

    if filtros.vegano:
        mask = df['ALIMENTO'].str.lower().str.contains(
            r'huevo|miel', na=False, regex=True)
        df = df[~mask]
        df = df[df['GRUPO'] != 'Huevos']

    if filtros.alergenos:
        import re as _re
        patron = '|'.join(_re.escape(a.lower()) for a in filtros.alergenos)
        mask = df['ALIMENTO'].str.lower().str.contains(patron, na=False, regex=True)
        df = df[~mask]

    return df


def optimizar_dieta(
    df_alimentos: pd.DataFrame,
    req: dict,
    filtros: Optional[FiltrosDieta] = None,
    min_por_grupo: Optional[dict] = None,
    max_por_grupo: Optional[dict] = None,
) -> ResultadoOptimizacion:

    if filtros is None:
        filtros = FiltrosDieta()

    _min_grupo = {**MIN_POR_GRUPO, **(min_por_grupo or {})}
    _max_grupo = {**MAX_POR_GRUPO, **(max_por_grupo or {})}

    # ── Aplicar restricciones etarias ────────────────────────────────────────
    rango_key = req.get('_rango_key', '')
    restriccion_etaria = RESTRICCIONES_ETARIAS.get(rango_key, {})
    aviso_etario = restriccion_etaria.get('aviso', '')

    # 0-6m: no correr LP
    if restriccion_etaria.get('solo_leche'):
        return ResultadoOptimizacion(
            exito=False,
            mensaje=restriccion_etaria.get('mensaje', ''),
            alimentos=pd.DataFrame(), aportes={}, costo_total=0,
            costo_mensual=0, fuente_precios='', req=req,
            infactibilidad_detalle='solo_leche_materna',
        )

    # Ajustar grupos excluidos por edad
    for grupo in restriccion_etaria.get('grupos_excluidos', []):
        _min_grupo[grupo] = 0

    # Ajustar máximos por edad
    for grupo, maximo in restriccion_etaria.get('max_por_grupo', {}).items():
        _max_grupo[grupo] = maximo

    if filtros.vegetariano or filtros.vegano:
        for g in ['Carnes', 'Aves', 'Pescados', 'Embutidos']:
            _min_grupo[g] = 0
    if filtros.vegano:
        _min_grupo['Lacteos'] = 0
        _min_grupo['Huevos'] = 0
    if filtros.sin_lactosa:
        _min_grupo['Lacteos'] = 0

    # ── Filtros ───────────────────────────────────────────────────────────────
    df = _aplicar_filtros(df_alimentos, filtros)

    if 'DISPONIBLE' in df.columns:
        df = df[df['DISPONIBLE'] == True]

    df = df.copy()
    if 'FACTOR_PRECIO' in df.columns:
        df['PRECIO_g'] = df['PRECIO_g'] * df['FACTOR_PRECIO']
        df['PRECIO_100G'] = df['PRECIO_g'] * 100

    # ── Caps de nutrientes (sanidad de datos) ─────────────────────────────────
    CAPS = {
        'FE_g':    0.25,
        'CA_g':    1.5,
        'VIT_A_g': 300.0,
        'VIT_C_g': 2.0,
    }
    for col, cap in CAPS.items():
        if col in df.columns:
            df[col] = df[col].clip(upper=cap)

    df = df.dropna(subset=['PRECIO_g', 'CAL_g'])
    df = df[df['CAL_g'] > 0]
    df = df[df['PRECIO_g'] > 0]
    df = df.reset_index(drop=True)
    n = len(df)

    if n == 0:
        return ResultadoOptimizacion(
            exito=False,
            mensaje="No hay alimentos disponibles con la configuración seleccionada.",
            alimentos=pd.DataFrame(), aportes={}, costo_total=0,
            costo_mensual=0, fuente_precios='', req=req
        )

    c = df['PRECIO_g'].values

    A_ub_rows = []
    b_ub_rows = []

    def col_g(col):
        return np.nan_to_num(df[col].values, nan=0.0) if col in df.columns else np.zeros(n)

    # ── Restricciones nutricionales ───────────────────────────────────────────
    A_ub_rows.append(-col_g('CAL_g'));    b_ub_rows.append(-req['energia_min'])
    A_ub_rows.append( col_g('CAL_g'));    b_ub_rows.append( req['energia_max'])
    A_ub_rows.append(-col_g('PR_g'));     b_ub_rows.append(-req['proteinas_min'])
    A_ub_rows.append(-col_g('GR_g'));     b_ub_rows.append(-req['grasas_min'])
    A_ub_rows.append( col_g('GR_g'));     b_ub_rows.append( req['grasas_max'])
    A_ub_rows.append(-col_g('HC_g'));     b_ub_rows.append(-req['hc_min'])
    A_ub_rows.append(-col_g('CA_g'));     b_ub_rows.append(-req['calc_min'])
    A_ub_rows.append(-col_g('FE_g'));     b_ub_rows.append(-req['hierro_min'])
    A_ub_rows.append(-col_g('VIT_A_g')); b_ub_rows.append(-req['vit_a_min_ui'])
    A_ub_rows.append(-col_g('VIT_C_g')); b_ub_rows.append(-req['vit_c_min'])
    A_ub_rows.append(-col_g('VIT_B1_g'));b_ub_rows.append(-req['vit_b1_min'])
    A_ub_rows.append(-col_g('VIT_B2_g'));b_ub_rows.append(-req['vit_b2_min'])

    # ── Fibra ─────────────────────────────────────────────────────────────────
    fibra_min = req.get('fibra_min', 0)
    if fibra_min > 0 and 'FIBRA_g' in df.columns:
        A_ub_rows.append(-col_g('FIBRA_g'))
        b_ub_rows.append(-fibra_min)

    # ── Zinc, Yodo, Selenio ──────────────────────────────────────────────────
    zinc_min = req.get('zinc_min', 0)
    if zinc_min > 0 and 'ZINC_g' in df.columns:
        A_ub_rows.append(-col_g('ZINC_g'))
        b_ub_rows.append(-zinc_min)

    yodo_min = req.get('yodo_min', 0)
    if yodo_min > 0 and 'YODO_g' in df.columns:
        A_ub_rows.append(-col_g('YODO_g'))
        b_ub_rows.append(-yodo_min)

    selenio_min = req.get('selenio_min', 0)
    if selenio_min > 0 and 'SELENIO_g' in df.columns:
        A_ub_rows.append(-col_g('SELENIO_g'))
        b_ub_rows.append(-selenio_min)

    # ── Límites superiores de seguridad (UL — OMS/IOM) ───────────────────────
    # Solo para nutrientes liposolubles o con riesgo de toxicidad.
    # Vitaminas hidrosolubles (C, B1, B2) no tienen UL restrictivo — sin techo.
    VIT_A_MAX  = req.get('vit_a_max_ui', 10_000)   # UL adultos: 10.000 UI
    HIERRO_MAX = req.get('hierro_max',   45.0)      # UL: 45 mg
    SELENIO_MAX= req.get('selenio_max',  300.0)     # UL: 400 µg; usamos 300 conservador
    ZINC_MAX   = req.get('zinc_max',     40.0)      # UL: 40 mg

    if 'VIT_A_g' in df.columns:
        A_ub_rows.append(col_g('VIT_A_g'))
        b_ub_rows.append(VIT_A_MAX)

    if 'FE_g' in df.columns:
        A_ub_rows.append(col_g('FE_g'))
        b_ub_rows.append(HIERRO_MAX)

    if 'SELENIO_g' in df.columns:
        A_ub_rows.append(col_g('SELENIO_g'))
        b_ub_rows.append(SELENIO_MAX)

    if 'ZINC_g' in df.columns:
        A_ub_rows.append(col_g('ZINC_g'))
        b_ub_rows.append(ZINC_MAX)

    # ── FIX: Peso total diario (saciedad) ─────────────────────────────────────
    rango_key = req.get('_rango_key', '')
    factor_peso = FACTOR_PESO_ETARIO.get(rango_key, 1.0)
    min_gramos = MIN_GRAMOS_TOTAL * factor_peso
    max_gramos = MAX_GRAMOS_TOTAL * factor_peso

    A_ub_rows.append(-np.ones(n));   b_ub_rows.append(-min_gramos)
    A_ub_rows.append( np.ones(n));   b_ub_rows.append( max_gramos)

    # ── Restricciones por grupo ───────────────────────────────────────────────
    for grupo in df['GRUPO'].unique():
        mask = (df['GRUPO'] == grupo).astype(float).values
        minimo = _min_grupo.get(grupo, 0)
        maximo = _max_grupo.get(grupo, 500)
        if minimo > 0:
            A_ub_rows.append(-mask); b_ub_rows.append(-minimo)
        A_ub_rows.append(mask);  b_ub_rows.append(maximo)

    if not filtros.vegetariano:
        mask_animal = df['GRUPO'].isin(
            ['Carnes', 'Aves', 'Pescados', 'Huevos']
        ).astype(float).values
        A_ub_rows.append(-mask_animal)
        b_ub_rows.append(-MIN_PROTEINA_ANIMAL)

    # ── Límites individuales ──────────────────────────────────────────────────
    from data.tabla_loader import LIMITES_INDIVIDUALES
    bounds = []
    for _, row in df.iterrows():
        nombre_l = row['ALIMENTO'].lower()
        max_g = 300
        for keyword, lim in LIMITES_INDIVIDUALES.items():
            if keyword in nombre_l:
                max_g = lim
                break
        bounds.append((0, max_g))

    # ── LP extendido con penalización de excesos ─────────────────────────────
    # Variables: [x_1..x_n, e_1..e_k] donde e_j = exceso sobre objetivo_j
    #
    # Objetivo: minimizar costo + λ × Σ(e_j / objetivo_j)
    #
    # Para cada nutriente con objetivo blando:
    #   Σ(nut_ij × x_i) - e_j ≤ objetivo_j  →  e_j ≥ aporte_j - objetivo_j
    #   e_j ≥ 0
    #
    # Esto hace que el LP prefiera dietas equilibradas sin sacrificar
    # el cumplimiento de los requerimientos mínimos.

    # Nutrientes a penalizar y sus objetivos (1.5× mínimo, salvo UL)
    NUTRIENTES_PENALIZAR = []
    UL = {
        'VIT_A_g':  10000 / 100,  # UL 10000 UI → /100g
        'FE_g':     45 / 100,
        'SELENIO_g': 300 / 100,
        'ZINC_g':   40 / 100,
    }
    # Objetivos y pesos de penalización por nutriente
    # objetivo: nivel "ideal" - LP penaliza superarlo
    # peso: cuánto pesa en el objetivo (mayor = más fuerte la penalización)
    NUTRIENTES_PENALIZAR_DEF = [
        # (col, mínimo/100,                          máximo_duro/100,            objetivo_mult, peso)
        ('CAL_g',    req['energia_min']/100,    req['energia_max']/100,       1.0,   2.0),
        ('PR_g',     req['proteinas_min']/100,  None,                         1.2,   3.0),  # proteínas: target 120%
        ('GR_g',     req['grasas_min']/100,     req['grasas_max']/100,        0.9,   2.0),
        ('HC_g',     req['hc_min']/100,         req.get('hc_max', req['hc_min']*4)/100, 1.3, 3.0),  # HC: target 130%
        ('CA_g',     req['calc_min']/100,       None,                         1.3,   1.5),
        ('FE_g',     req['hierro_min']/100,     UL['FE_g'],                   1.2,   2.0),
        ('VIT_A_g',  req['vit_a_min_ui']/100,  UL['VIT_A_g'],                1.1,   4.0),  # Vit A: target 110%, peso alto
        ('VIT_C_g',  req['vit_c_min']/100,     None,                         1.5,   1.0),  # C: inofensivo, peso bajo
        ('VIT_B1_g', req['vit_b1_min']/100,    None,                         1.05,  3.0),  # B1: peso aumentado
        ('VIT_B2_g', req['vit_b2_min']/100,    None,                         1.05,  4.0),  # B2: peso alto, objetivo justo al mínimo
        ('FIBRA_g',  req.get('fibra_min',25)/100, None,                      1.5,   1.0),
        ('ZINC_g',   req.get('zinc_min',8)/100,   UL['ZINC_g'],              1.3,   1.5),
        ('SELENIO_g',req.get('selenio_min',55)/100, UL['SELENIO_g'],         1.3,   1.5),
    ]

    for col_n, min_val, ub_val, mult, peso in NUTRIENTES_PENALIZAR_DEF:
        if col_n not in df.columns or min_val <= 0:
            continue
        objetivo = min_val * mult
        if ub_val:
            objetivo = min(objetivo, ub_val * 0.85)
        NUTRIENTES_PENALIZAR.append((col_n, objetivo, peso))

    k = len(NUTRIENTES_PENALIZAR)

    # λ más fuerte: 25% del presupuesto de costo
    precio_medio = float(np.mean(c[c > 0])) if np.any(c > 0) else 1.0
    lambda_base = precio_medio * n * 0.25 / max(k, 1)

    # Extender vector de costos: [costos alimentos, penalización excesos]
    pen_coefs = np.array([lambda_base * peso / max(obj * 100, 1e-6)
                          for _, obj, peso in NUTRIENTES_PENALIZAR])
    c_ext = np.concatenate([c, pen_coefs])

    # Extender A_ub: columnas de exceso son 0 para restricciones existentes
    zeros_k = np.zeros((len(A_ub_rows), k))
    A_ub_base = np.column_stack([np.array(A_ub_rows), zeros_k])

    # Agregar restricciones de exceso: nut_j × x - e_j ≤ objetivo_j
    for j, (col_n, objetivo, _peso) in enumerate(NUTRIENTES_PENALIZAR):
        nut_row = col_g(col_n)
        slack_row = np.zeros(k)
        slack_row[j] = -1.0  # -e_j
        A_ub_base = np.vstack([
            A_ub_base,
            np.concatenate([nut_row, slack_row])
        ])
        b_ub_rows.append(objetivo * 100)  # objetivo en unidades absolutas

    b_ub_ext = np.array(b_ub_rows)

    # Bounds extendidos: [alimentos 0..max_g, excesos 0..∞]
    bounds_ext = bounds + [(0, None)] * k

    A_ub = A_ub_base
    b_ub = b_ub_ext

    res = linprog(
        c=c_ext, A_ub=A_ub, b_ub=b_ub, bounds=bounds_ext,
        method='highs', options={'disp': False, 'time_limit': 45.0}
    )

    # Extraer solo las variables de alimentos (descartar excesos)
    if res.success:
        res_x_original = res.x[:n]
    else:
        res_x_original = None

    if not res.success:
        return ResultadoOptimizacion(
            exito=False,
            mensaje=(
                f"No se encontró solución con todos los requerimientos. "
                f"Solver: {res.message}. "
                f"Posible causa: restricciones demasiado estrictas o "
                f"datos nutricionales insuficientes."
            ),
            alimentos=pd.DataFrame(), aportes={}, costo_total=0,
            costo_mensual=0, fuente_precios='', req=req,
            infactibilidad_detalle=res.message,
        )

    # ── Procesar resultado (solo variables de alimentos, sin excesos) ─────────
    res_x_food = res.x[:n]  # descartar variables de holgura de exceso
    df_res = df.copy()
    df_res['GRAMOS'] = res_x_food
    df_res = df_res[df_res['GRAMOS'] >= 2.0].copy()

    nutrientes_calc = [
        ('CAL', 'CAL_g'), ('PR', 'PR_g'), ('GR', 'GR_g'), ('HC', 'HC_g'),
        ('CA', 'CA_g'), ('FE', 'FE_g'), ('VIT_A', 'VIT_A_g'),
        ('VIT_C', 'VIT_C_g'), ('VIT_B1', 'VIT_B1_g'), ('VIT_B2', 'VIT_B2_g'),
    ]
    if 'FIBRA_g' in df_res.columns:
        nutrientes_calc.append(('FIBRA', 'FIBRA_g'))
    if 'ZINC_g' in df_res.columns:
        nutrientes_calc.append(('ZINC', 'ZINC_g'))
    if 'YODO_g' in df_res.columns:
        nutrientes_calc.append(('YODO', 'YODO_g'))
    if 'SELENIO_g' in df_res.columns:
        nutrientes_calc.append(('SELENIO', 'SELENIO_g'))

    for col, col_g_name in nutrientes_calc:
        df_res[f'APORTE_{col}'] = df_res['GRAMOS'] * np.nan_to_num(
            df_res[col_g_name].values if col_g_name in df_res.columns else np.zeros(len(df_res)),
            nan=0.0
        )

    df_res['COSTO'] = df_res['GRAMOS'] * df_res['PRECIO_g']
    costo_total = df_res['COSTO'].sum()

    aportes = {
        'energia_kcal':  df_res['APORTE_CAL'].sum(),
        'proteinas_g':   df_res['APORTE_PR'].sum(),
        'grasas_g':      df_res['APORTE_GR'].sum(),
        'hc_g':          df_res['APORTE_HC'].sum(),
        'calcio_mg':     df_res['APORTE_CA'].sum(),
        'hierro_mg':     df_res['APORTE_FE'].sum(),
        'vit_a_ui':      df_res['APORTE_VIT_A'].sum(),
        'vit_c_mg':      df_res['APORTE_VIT_C'].sum(),
        'vit_b1_mg':     df_res['APORTE_VIT_B1'].sum(),
        'vit_b2_mg':     df_res['APORTE_VIT_B2'].sum(),
        'fibra_g':       df_res['APORTE_FIBRA'].sum() if 'APORTE_FIBRA' in df_res.columns else 0,
        'zinc_mg':       df_res['APORTE_ZINC'].sum() if 'APORTE_ZINC' in df_res.columns else 0,
        'yodo_ug':       df_res['APORTE_YODO'].sum() if 'APORTE_YODO' in df_res.columns else 0,
        'selenio_ug':    df_res['APORTE_SELENIO'].sum() if 'APORTE_SELENIO' in df_res.columns else 0,
        'gramos_total':  df_res['GRAMOS'].sum(),
    }

    df_res = df_res.sort_values(['GRUPO', 'GRAMOS'], ascending=[True, False])

    cols_out = ['NOMBRE_COMPLETO', 'GRUPO', 'GRAMOS', 'COSTO',
                'APORTE_CAL', 'APORTE_PR', 'APORTE_GR', 'APORTE_HC',
                'APORTE_CA', 'APORTE_FE', 'APORTE_VIT_C']
    if 'APORTE_FIBRA' in df_res.columns:
        cols_out.append('APORTE_FIBRA')

    return ResultadoOptimizacion(
        exito=True,
        mensaje=aviso_etario if aviso_etario else "Optimización exitosa",
        alimentos=df_res[cols_out],
        aportes=aportes,
        costo_total=costo_total,
        costo_mensual=costo_total * 30,
        fuente_precios='SEPA',
        req=req,
    )


def imprimir_resultado(resultado: ResultadoOptimizacion) -> None:
    if not resultado.exito:
        print(f"\n⚠ {resultado.mensaje}")
        return

    req = resultado.req
    ap  = resultado.aportes

    print("\n" + "═" * 60)
    print(f"  PLAN NUTRICIONAL DIARIO ÓPTIMO")
    print("═" * 60)
    print(f"\n{'Alimento':<35} {'Grupo':<15} {'Gramos':>8} {'Costo $':>10}")
    print("-" * 70)

    for grupo in resultado.alimentos['GRUPO'].unique():
        sub = resultado.alimentos[resultado.alimentos['GRUPO'] == grupo]
        print(f"\n  ── {grupo} ──")
        for _, row in sub.iterrows():
            print(f"  {row['NOMBRE_COMPLETO']:<33} {row['GRUPO']:<15} "
                  f"{row['GRAMOS']:>7.0f}g {row['COSTO']:>9.0f}")

    print(f"\n  Peso total diario: {ap['gramos_total']:.0f}g")

    print("\n" + "─" * 60)
    print("  APORTES vs REQUERIMIENTOS OMS")
    print("─" * 60)

    def pct_ok(real, minimo, maximo=None):
        pct = real / minimo * 100 if minimo > 0 else 100
        if maximo and real > maximo * 1.02:
            return f"⚠  EXCEDE  {real:.1f} / máx {maximo:.0f}  ({pct:.0f}%)"
        icono = "✓" if pct >= 99.5 else "✗"
        return f"{icono}  {real:.1f} / {minimo:.1f}  ({pct:.0f}%)"

    print(f"  Energía (kcal):    {pct_ok(ap['energia_kcal'],  req['energia_min'],  req['energia_max'])}")
    print(f"  Proteínas (g):     {pct_ok(ap['proteinas_g'],   req['proteinas_min'])}")
    print(f"  Grasas (g):        {pct_ok(ap['grasas_g'],      req['grasas_min'],   req['grasas_max'])}")
    print(f"  Carbohidratos (g): {pct_ok(ap['hc_g'],          req['hc_min'])}")
    print(f"  Calcio (mg):       {pct_ok(ap['calcio_mg'],     req['calc_min'])}")
    print(f"  Hierro (mg):       {pct_ok(ap['hierro_mg'],     req['hierro_min'])}")
    print(f"  Vitamina A (UI):   {pct_ok(ap['vit_a_ui'],      req['vit_a_min_ui'])}")
    print(f"  Vitamina C (mg):   {pct_ok(ap['vit_c_mg'],      req['vit_c_min'])}")
    print(f"  Vitamina B1 (mg):  {pct_ok(ap['vit_b1_mg'],     req['vit_b1_min'])}")
    print(f"  Vitamina B2 (mg):  {pct_ok(ap['vit_b2_mg'],     req['vit_b2_min'])}")
    if ap.get('fibra_g', 0) > 0:
        fibra_min = req.get('fibra_min', 25)
        print(f"  Fibra (g):         {pct_ok(ap['fibra_g'], fibra_min)}")

    print("\n" + "─" * 60)
    print(f"  Costo diario:   ${resultado.costo_total:>10,.0f}")
    print(f"  Costo mensual:  ${resultado.costo_mensual:>10,.0f}")
    print("═" * 60)
