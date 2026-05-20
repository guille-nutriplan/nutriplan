"""
optimizer.py
Motor de optimización de dieta por programación lineal.

Resuelve:
  min  Σ precio_g[i] · x[i]          (minimizar costo)
  s.t.
    Σ CAL_g[i]    · x[i] >= energia_min
    Σ CAL_g[i]    · x[i] <= energia_max
    Σ PR_g[i]     · x[i] >= proteinas_min
    Σ GR_g[i]     · x[i] >= grasas_min
    Σ GR_g[i]     · x[i] <= grasas_max
    Σ HC_g[i]     · x[i] >= hc_min
    Σ CA_g[i]     · x[i] >= calc_min
    Σ FE_g[i]     · x[i] >= hierro_min
    Σ VIT_A_g[i]  · x[i] >= vit_a_min
    Σ VIT_C_g[i]  · x[i] >= vit_c_min
    Σ VIT_B1_g[i] · x[i] >= vit_b1_min
    Σ VIT_B2_g[i] · x[i] >= vit_b2_min
    Σ x[i] para grupo g >= minimo_grupo[g]   ∀g
    0 <= x[i] <= max_por_alimento[i]          ∀i

donde x[i] = gramos del alimento i en el día.
"""

import numpy as np
import pandas as pd
from scipy.optimize import linprog
from dataclasses import dataclass, field
from typing import Optional


# ─── Parámetros del optimizador ───────────────────────────────────────────────

# Máximos de consumo diario por grupo (g/día) — evita soluciones degeneradas
MAX_POR_GRUPO = {
    'Cereales':    400,
    'Leguminosas': 200,
    'Hortalizas':  500,
    'Frutas':      400,
    'FrutosSecos': 100,
    'Lacteos':     500,
    'Huevos':      150,   # ≈ 2 huevos grandes
    'Aceites':      60,
    'Azucares':    100,
    'Pescados':    300,
    'Carnes':      250,
    'Embutidos':   100,
    'Aves':        300,
}

# Mínimos de consumo diario por grupo (g/día) — diversidad mínima
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

# Mínimo combinado de proteínas animales (Carnes + Aves + Pescados + Huevos)
MIN_PROTEINA_ANIMAL = 30   # g/día (cero para veganos)


@dataclass
class FiltrosDieta:
    celiaco: bool = False
    sin_lactosa: bool = False
    vegetariano: bool = False
    vegano: bool = False
    alergenos: list[str] = field(default_factory=list)

    def __post_init__(self):
        # Vegano implica vegetariano y sin lactosa
        if self.vegano:
            self.vegetariano = True
            self.sin_lactosa = True


@dataclass
class ResultadoOptimizacion:
    exito: bool
    mensaje: str
    alimentos: pd.DataFrame          # Alimentos seleccionados con gramos y aportes
    aportes: dict                    # Totales de nutrientes
    costo_total: float               # ARS/día
    costo_mensual: float             # ARS/mes
    fuente_precios: str              # 'SEPA' o 'referencia_local'
    req: dict                        # Requerimientos OMS aplicados
    infactibilidad_detalle: Optional[str] = None


def _aplicar_filtros(df: pd.DataFrame, filtros: FiltrosDieta) -> pd.DataFrame:
    """Filtra el DataFrame de alimentos según restricciones dietarias."""
    df = df.copy()

    if filtros.celiaco:
        # Excluir por nombre de ingrediente explícito
        mask_ingr = df['ALIMENTO'].str.lower().str.contains(
            r'trigo|cebada|centeno|avena|sémola|semola', na=False, regex=True)
        # Excluir preparaciones implícitamente de gluten en el grupo Cereales
        # (macarrones, fideos, pasta, galletas, pan — excepto arroz y maíz)
        mask_gluten_cereales = (
            (df['GRUPO'] == 'Cereales') &
            df['ALIMENTO'].str.lower().str.contains(
                r'macarr|fideos|pasta|galleta|buñuelo|semola|sémola'
                r'|pan de trigo|pan de avena|pan de cebada|pan de centeno'
                r'|pan de viena|tarta',
                na=False, regex=True
            )
        )
        # Pan: excluir todos excepto pan de maíz y pan de arroz
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
    """
    Ejecuta la optimización LP para minimizar costo cubriendo requerimientos OMS.

    Parameters
    ----------
    df_alimentos : DataFrame con nutrientes por gramo y precio_g
    req          : dict de requerimientos (de who_requirements.WHO_REQUIREMENTS)
    filtros      : FiltrosDieta (None = sin restricciones)
    min/max_por_grupo : override de los defaults globales

    Returns
    -------
    ResultadoOptimizacion
    """
    import re   # importar aquí para evitar circular en módulo

    if filtros is None:
        filtros = FiltrosDieta()

    _min_grupo = {**MIN_POR_GRUPO, **(min_por_grupo or {})}
    _max_grupo = {**MAX_POR_GRUPO, **(max_por_grupo or {})}

    # Ajustar mínimos de grupos excluidos por filtros
    if filtros.vegetariano or filtros.vegano:
        for g in ['Carnes', 'Aves', 'Pescados', 'Embutidos']:
            _min_grupo[g] = 0
    if filtros.vegano:
        _min_grupo['Lacteos'] = 0
        _min_grupo['Huevos'] = 0
    if filtros.sin_lactosa:
        _min_grupo['Lacteos'] = 0

    # ── Aplicar filtros de dieta ──────────────────────────────────────────────
    df = _aplicar_filtros(df_alimentos, filtros)

    # ── FIX 2: excluir alimentos no disponibles en supermercados ─────────────
    if 'DISPONIBLE' in df.columns:
        df = df[df['DISPONIBLE'] == True]

    # ── FIX 3: aplicar factor de precio a formas concentradas/secas ──────────
    df = df.copy()
    if 'FACTOR_PRECIO' in df.columns:
        df['PRECIO_g'] = df['PRECIO_g'] * df['FACTOR_PRECIO']
        df['PRECIO_100G'] = df['PRECIO_g'] * 100

    # ── Sanidad de nutrientes: cap de contribución por gramo ─────────────────
    # Evita que errores de la tabla (ej: achicoria tostada con 58 mg Fe/100g)
    # distorsionen la solución aunque hayan pasado el filtro de exclusión.
    # Los caps son generosos — ningún alimento real los debería superar.
    CAPS_NUTRIENTE_POR_100G = {
        'FE_g':    0.25,    # 25 mg Fe / 100g (hígado vacuno real: ~10-20 mg)
        'CA_g':    1.5,     # 150 mg Ca / 100g (queso duro: ~1000 mg, ok)
        'VIT_A_g': 300.0,   # 30000 UI / 100g (hígado pollo: ~15000 UI, ok)
        'VIT_C_g': 2.0,     # 200 mg Vit C / 100g (kiwi/acerola: ~90 mg, limita exóticos)
    }
    for col, cap in CAPS_NUTRIENTE_POR_100G.items():
        if col in df.columns:
            df[col] = df[col].clip(upper=cap)
            # Actualizar también la columna sin _g
            base = col.replace('_g', '')
            if base in df.columns:
                df[base] = df[col] * 100

    # ── Filtrar alimentos sin precio o sin calorías ───────────────────────────
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

    # ── Función objetivo: minimizar costo ─────────────────────────────────────
    c = df['PRECIO_g'].values

    # ── Restricciones de nutrientes ───────────────────────────────────────────
    # Para linprog: A_ub @ x <= b_ub
    # Restricción >= se expresa como -A @ x <= -b

    A_ub_rows = []
    b_ub_rows = []

    def nutriente_col(col):
        """Devuelve vector de nutriente por gramo, reemplazando NaN por 0."""
        return np.nan_to_num(df[col].values, nan=0.0)

    # Energía mínima
    A_ub_rows.append(-nutriente_col('CAL_g'))
    b_ub_rows.append(-req['energia_min'])

    # Energía máxima
    A_ub_rows.append(nutriente_col('CAL_g'))
    b_ub_rows.append(req['energia_max'])

    # Proteínas mínima
    A_ub_rows.append(-nutriente_col('PR_g'))
    b_ub_rows.append(-req['proteinas_min'])

    # Grasas mínima y máxima
    A_ub_rows.append(-nutriente_col('GR_g'))
    b_ub_rows.append(-req['grasas_min'])
    A_ub_rows.append(nutriente_col('GR_g'))
    b_ub_rows.append(req['grasas_max'])

    # Hidratos mínimos
    A_ub_rows.append(-nutriente_col('HC_g'))
    b_ub_rows.append(-req['hc_min'])

    # Calcio mínimo
    A_ub_rows.append(-nutriente_col('CA_g'))
    b_ub_rows.append(-req['calc_min'])

    # Hierro mínimo
    A_ub_rows.append(-nutriente_col('FE_g'))
    b_ub_rows.append(-req['hierro_min'])

    # Vitamina A mínima (en UI)
    A_ub_rows.append(-nutriente_col('VIT_A_g'))
    b_ub_rows.append(-req['vit_a_min_ui'])

    # Vitamina C mínima (en mg)
    A_ub_rows.append(-nutriente_col('VIT_C_g'))
    b_ub_rows.append(-req['vit_c_min'])

    # Vitamina B1 mínima (en mg)
    A_ub_rows.append(-nutriente_col('VIT_B1_g'))
    b_ub_rows.append(-req['vit_b1_min'])

    # Vitamina B2 mínima (en mg)
    A_ub_rows.append(-nutriente_col('VIT_B2_g'))
    b_ub_rows.append(-req['vit_b2_min'])

    # ── Restricciones por grupo ───────────────────────────────────────────────
    for grupo in df['GRUPO'].unique():
        mask_grupo = (df['GRUPO'] == grupo).astype(float).values

        # Mínimo del grupo
        minimo = _min_grupo.get(grupo, 0)
        if minimo > 0:
            A_ub_rows.append(-mask_grupo)
            b_ub_rows.append(-minimo)

        # Máximo del grupo
        maximo = _max_grupo.get(grupo, 500)
        A_ub_rows.append(mask_grupo)
        b_ub_rows.append(maximo)

    # Mínimo proteína animal (si no es vegetariano/vegano)
    if not filtros.vegetariano:
        mask_animal = df['GRUPO'].isin(
            ['Carnes', 'Aves', 'Pescados', 'Huevos']
        ).astype(float).values
        A_ub_rows.append(-mask_animal)
        b_ub_rows.append(-MIN_PROTEINA_ANIMAL)

    # ── Límites por alimento individual ──────────────────────────────────────
    # Default: (0, 300g). Para condimentos/hierbas: límite individual más bajo.
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

    # ── Resolver ─────────────────────────────────────────────────────────────
    A_ub = np.array(A_ub_rows)
    b_ub = np.array(b_ub_rows)

    res = linprog(
        c=c,
        A_ub=A_ub,
        b_ub=b_ub,
        bounds=bounds,
        method='highs',
        options={'disp': False, 'time_limit': 30.0}
    )

    if not res.success:
        # Intentar con restricciones relajadas (solo energía y proteínas)
        return _intentar_fallback(df, req, filtros, res.message)

    # ── Procesar resultado ────────────────────────────────────────────────────
    df_res = df.copy()
    df_res['GRAMOS'] = res.x

    # Filtrar alimentos con cantidad insignificante (< 2g)
    df_res = df_res[df_res['GRAMOS'] >= 2.0].copy()

    # Calcular aportes
    for col, col_g in [('CAL', 'CAL_g'), ('PR', 'PR_g'), ('GR', 'GR_g'),
                        ('HC', 'HC_g'), ('CA', 'CA_g'), ('FE', 'FE_g'),
                        ('VIT_A', 'VIT_A_g'), ('VIT_C', 'VIT_C_g'),
                        ('VIT_B1', 'VIT_B1_g'), ('VIT_B2', 'VIT_B2_g')]:
        df_res[f'APORTE_{col}'] = df_res['GRAMOS'] * np.nan_to_num(df_res[col_g].values, nan=0.0)

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
    }

    fuente = df_res['FUENTE'].iloc[0] if 'FUENTE' in df_res.columns else 'desconocida'

    # Ordenar por grupo y gramos descendente
    df_res = df_res.sort_values(['GRUPO', 'GRAMOS'], ascending=[True, False])

    return ResultadoOptimizacion(
        exito=True,
        mensaje="Optimización exitosa",
        alimentos=df_res[['NOMBRE_COMPLETO', 'GRUPO', 'GRAMOS', 'COSTO',
                           'APORTE_CAL', 'APORTE_PR', 'APORTE_GR', 'APORTE_HC',
                           'APORTE_CA', 'APORTE_FE', 'APORTE_VIT_C']],
        aportes=aportes,
        costo_total=costo_total,
        costo_mensual=costo_total * 30,
        fuente_precios='SEPA',
        req=req,
    )


def _intentar_fallback(df, req, filtros, mensaje_error):
    """
    Si el LP completo es infactible, intenta con restricciones relajadas
    para dar un resultado parcial + diagnóstico.
    """
    # Restricciones mínimas: solo energía y proteínas
    req_min = {
        'energia_min': req['energia_min'],
        'energia_max': req['energia_max'] * 1.3,  # más tolerante
        'proteinas_min': req['proteinas_min'] * 0.8,
        'grasas_min': req['grasas_min'] * 0.5,
        'grasas_max': req['grasas_max'] * 1.5,
        'hc_min': req['hc_min'] * 0.5,
        'calc_min': 0,
        'hierro_min': 0,
        'vit_a_min_ui': 0,
        'vit_c_min': 0,
        'vit_b1_min': 0,
        'vit_b2_min': 0,
    }

    return ResultadoOptimizacion(
        exito=False,
        mensaje=(
            f"No se encontró solución con todos los requerimientos nutricionales. "
            f"Detalle del solver: {mensaje_error}. "
            f"Posibles causas: precios de referencia inadecuados, "
            f"restricciones de dieta demasiado estrictas, o "
            f"datos nutricionales insuficientes para ciertos micronutrientes."
        ),
        alimentos=pd.DataFrame(),
        aportes={},
        costo_total=0,
        costo_mensual=0,
        fuente_precios='',
        req=req,
        infactibilidad_detalle=mensaje_error,
    )


def imprimir_resultado(resultado: ResultadoOptimizacion) -> None:
    """Imprime el resultado de optimización en consola."""
    if not resultado.exito:
        print(f"\n⚠ {resultado.mensaje}")
        return

    req = resultado.req
    ap = resultado.aportes

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

    print("\n" + "─" * 60)
    print("  APORTES NUTRICIONALES vs REQUERIMIENTOS OMS")
    print("─" * 60)

    def pct_ok(real, minimo, maximo=None):
        if maximo and real > maximo * 1.02:
            return f"⚠ EXCEDE ({real:.0f} > {maximo:.0f})"
        pct = real / minimo * 100 if minimo > 0 else 100
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

    print("\n" + "─" * 60)
    fuente_label = "SEPA (datos.gob.ar)" if resultado.fuente_precios == 'SEPA' else "Precios de referencia"
    print(f"  Costo diario estimado:   ${resultado.costo_total:>10,.0f}")
    print(f"  Costo mensual estimado:  ${resultado.costo_mensual:>10,.0f}")
    print(f"  Fuente de precios: {fuente_label}")
    print("═" * 60)
