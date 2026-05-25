"""
api/main.py — NutriPlan v2.0 — Backend FastAPI

Endpoints:
  GET  /                          → health check
  GET  /api/provincias            → lista de provincias
  GET  /api/rangos                → grupos etarios OMS
  POST /api/plan                  → calcular plan nutricional óptimo
"""

import sys
from pathlib import Path

# Asegurar que el raíz del proyecto esté en el path
sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import Optional
import traceback

from data.tabla_loader import cargar_tabla
from data.sepa_client import obtener_precios_sepa, aplicar_precios, _precios_referencia, PROVINCIAS, sepa_cache
from data.who_requirements import WHO_REQUIREMENTS, WHO_GROUPS_UI
from engine.optimizer import optimizar_dieta, FiltrosDieta

# ─── Inicializar app ───────────────────────────────────────────────────────────
app = FastAPI(
    title="NutriPlan API",
    description="Planificador de dieta nutricional económica para Argentina — OMS",
    version="2.0.0",
)

# CORS: permite que el frontend React (cualquier origen) consulte la API
@app.on_event("startup")
async def startup_event():
    """Inicia la descarga de precios SEPA en background al arrancar el servidor."""
    sepa_cache.iniciar(delay_segundos=8)
    sepa_cache.programar_refresco(intervalo_horas=24)
    print("[API] Servidor iniciado. Descarga SEPA programada.")


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Cargar tabla una sola vez al iniciar (costoso, no repetir por request) ───
RUTA_EXCEL = Path(__file__).parent.parent / "data" / "tabla_composicion_alimentos.xlsx"
print("Cargando tabla nutricional...")
DF_BASE = cargar_tabla(RUTA_EXCEL)
print("✓ Tabla lista")


# ─── Modelos de entrada / salida ──────────────────────────────────────────────

class FiltrosRequest(BaseModel):
    celiaco:     bool = False
    sin_lactosa: bool = False
    vegetariano: bool = False
    vegano:      bool = False
    alergenos:   list[str] = Field(default_factory=list)


class PlanRequest(BaseModel):
    provincia_codigo: Optional[str] = Field(
        None,
        description="Código de provincia (ej: 'AR-Q'). None = precios nacionales."
    )
    rango_etario: str = Field(
        ...,
        description="Clave del rango etario (ej: '4-6', '30-59M', 'embarazada')"
    )
    filtros: FiltrosRequest = Field(default_factory=FiltrosRequest)


class AlimentoItem(BaseModel):
    nombre:    str
    grupo:     str
    gramos:    float
    costo_ars: float
    aporte_cal: float
    aporte_pr:  float
    aporte_gr:  float
    aporte_hc:  float


class AportesNutricionales(BaseModel):
    energia_kcal: float
    proteinas_g:  float
    grasas_g:     float
    hc_g:         float
    calcio_mg:    float
    hierro_mg:    float
    vit_a_ui:     float
    vit_c_mg:     float
    vit_b1_mg:    float
    vit_b2_mg:    float
    fibra_g:      float
    gramos_total: float
    zinc_mg:      float
    yodo_ug:      float
    selenio_ug:   float


class ReqOMS(BaseModel):
    energia_min:  float
    energia_max:  float
    proteinas_min: float
    grasas_min:   float
    grasas_max:   float
    hc_min:       float
    calc_min:     float
    hierro_min:   float
    vit_a_min_ui: float
    vit_c_min:    float
    vit_b1_min:   float
    vit_b2_min:   float
    hc_max:       float = 520.0
    fibra_max:    float = 50.0
    calc_max:     float = 2500.0
    hierro_max:   float = 45.0
    vit_a_max_ui: float = 10000.0
    vit_c_max:    float = 2000.0
    zinc_min:     float
    zinc_max:     float = 40.0
    yodo_min:     float
    yodo_max:     float = 600.0
    selenio_min:  float
    selenio_max:  float = 300.0


class PlanResponse(BaseModel):
    exito:             bool
    mensaje:           str
    solo_leche_materna: bool = False
    provincia:         str
    rango_label:       str
    alimentos:      list[AlimentoItem]
    aportes:        AportesNutricionales
    req_oms:        ReqOMS
    costo_diario:   float
    costo_mensual:  float
    fuente_precios: str


# ─── Endpoints ────────────────────────────────────────────────────────────────

@app.get("/", tags=["Status"])
def health_check():
    return {
        "status": "ok",
        "app": "NutriPlan API",
        "version": "2.0.0",
        "alimentos_cargados": len(DF_BASE),
    }


@app.get("/api/provincias", tags=["Configuración"])
def get_provincias():
    """Lista de provincias disponibles para filtro de precios SEPA."""
    return {
        "provincias": [
            {"codigo": cod, "nombre": nombre}
            for cod, nombre in sorted(PROVINCIAS.items(), key=lambda x: x[1])
        ]
    }


@app.get("/api/rangos", tags=["Configuración"])
def get_rangos():
    """Grupos etarios OMS con sus requerimientos."""
    grupos = []
    for grupo_info in WHO_GROUPS_UI:
        rangos = []
        for rango in grupo_info['rangos']:
            req = WHO_REQUIREMENTS[rango]
            rangos.append({
                "clave":  rango,
                "label":  req["label"],
                "nota":   req.get("nota", ""),
                "energia_min": req["energia_min"],
                "energia_max": req["energia_max"],
            })
        grupos.append({
            "grupo":  grupo_info["grupo"],
            "rangos": rangos,
        })
    return {"grupos": grupos}


@app.post("/api/plan", response_model=PlanResponse, tags=["Optimización"])
def calcular_plan(body: PlanRequest):
    """
    Calcula el plan nutricional diario óptimo (mínimo costo, requerimientos OMS).

    - **provincia_codigo**: código de provincia para precios SEPA locales
    - **rango_etario**: clave del grupo etario OMS
    - **filtros**: restricciones dietarias opcionales
    """
    # Validar rango etario
    if body.rango_etario not in WHO_REQUIREMENTS:
        raise HTTPException(
            status_code=400,
            detail=f"Rango etario '{body.rango_etario}' no válido. "
                   f"Opciones: {list(WHO_REQUIREMENTS.keys())}"
        )

    req = WHO_REQUIREMENTS[body.rango_etario]

    # Usar caché SEPA si está disponible, sino referencia
    df_precios = sepa_cache.get_precios(
        provincia_codigo=body.provincia_codigo if body.provincia_codigo else None
    )
    nombre_provincia = PROVINCIAS.get(body.provincia_codigo, "Nacional") if body.provincia_codigo else "Nacional"

    # Aplicar precios al DataFrame
    df = aplicar_precios(DF_BASE.copy(), df_precios)

    # Construir filtros
    filtros = FiltrosDieta(
        celiaco=body.filtros.celiaco,
        sin_lactosa=body.filtros.sin_lactosa,
        vegetariano=body.filtros.vegetariano,
        vegano=body.filtros.vegano,
        alergenos=body.filtros.alergenos,
    )

    # Optimizar
    try:
        resultado = optimizar_dieta(df, req, filtros)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error en optimización: {str(e)}\n{traceback.format_exc()}"
        )

    if not resultado.exito:
        solo_leche = resultado.infactibilidad_detalle == 'solo_leche_materna'
        return PlanResponse(
            exito=False,
            solo_leche_materna=solo_leche,
            mensaje=resultado.mensaje,
            provincia=nombre_provincia,
            rango_label=req["label"],
            alimentos=[],
            aportes=AportesNutricionales(**{k: 0 for k in AportesNutricionales.model_fields}),
            req_oms=ReqOMS(
                energia_min=req["energia_min"], energia_max=req["energia_max"],
                proteinas_min=req["proteinas_min"],
                grasas_min=req["grasas_min"], grasas_max=req["grasas_max"],
                hc_min=req["hc_min"], calc_min=req["calc_min"],
                hierro_min=req["hierro_min"], vit_a_min_ui=req["vit_a_min_ui"],
                vit_c_min=req["vit_c_min"], vit_b1_min=req["vit_b1_min"],
                vit_b2_min=req["vit_b2_min"],
                hc_max=req.get("hc_max", req.get("hc_min", 130) * 4),
                fibra_max=req.get("fibra_max", 50),
                calc_max=req.get("calc_max", 2500),
                hierro_max=req.get("hierro_max", 45),
                vit_a_max_ui=req.get("vit_a_max_ui", 10000),
                vit_c_max=req.get("vit_c_max", 2000),
                zinc_min=req.get("zinc_min", 0),
                zinc_max=req.get("zinc_max", 40),
                yodo_min=req.get("yodo_min", 0),
                yodo_max=req.get("yodo_max", 600),
                selenio_min=req.get("selenio_min", 0),
                selenio_max=req.get("selenio_max", 300),
            ),
            costo_diario=0,
            costo_mensual=0,
            fuente_precios="error",
        )

    # Serializar alimentos
    alimentos_out = []
    for _, row in resultado.alimentos.iterrows():
        alimentos_out.append(AlimentoItem(
            nombre=row["NOMBRE_COMPLETO"],
            grupo=row["GRUPO"],
            gramos=round(row["GRAMOS"], 1),
            costo_ars=round(row["COSTO"], 0),
            aporte_cal=round(row["APORTE_CAL"], 1),
            aporte_pr=round(row["APORTE_PR"], 1),
            aporte_gr=round(row["APORTE_GR"], 1),
            aporte_hc=round(row["APORTE_HC"], 1),
        ))

    ap = resultado.aportes

    return PlanResponse(
        exito=True,
        solo_leche_materna=False,
        mensaje=resultado.mensaje if resultado.mensaje != "Optimización exitosa" else "Plan calculado correctamente",
        provincia=nombre_provincia,
        rango_label=req["label"],
        alimentos=alimentos_out,
        aportes=AportesNutricionales(
            energia_kcal=round(ap["energia_kcal"], 1),
            proteinas_g=round(ap["proteinas_g"], 1),
            grasas_g=round(ap["grasas_g"], 1),
            hc_g=round(ap["hc_g"], 1),
            calcio_mg=round(ap["calcio_mg"], 1),
            hierro_mg=round(ap["hierro_mg"], 2),
            vit_a_ui=round(ap["vit_a_ui"], 0),
            vit_c_mg=round(ap["vit_c_mg"], 1),
            vit_b1_mg=round(ap["vit_b1_mg"], 2),
            vit_b2_mg=round(ap["vit_b2_mg"], 2),
            fibra_g=round(ap.get("fibra_g", 0), 1),
            gramos_total=round(ap.get("gramos_total", 0), 0),
            zinc_mg=round(ap.get("zinc_mg", 0), 2),
            yodo_ug=round(ap.get("yodo_ug", 0), 1),
            selenio_ug=round(ap.get("selenio_ug", 0), 1),
        ),
        req_oms=ReqOMS(
            energia_min=req["energia_min"], energia_max=req["energia_max"],
            proteinas_min=req["proteinas_min"],
            grasas_min=req["grasas_min"], grasas_max=req["grasas_max"],
            hc_min=req["hc_min"], calc_min=req["calc_min"],
            hierro_min=req["hierro_min"], vit_a_min_ui=req["vit_a_min_ui"],
            vit_c_min=req["vit_c_min"], vit_b1_min=req["vit_b1_min"],
            vit_b2_min=req["vit_b2_min"],
                hc_max=req.get("hc_max", req.get("hc_min", 130) * 4),
                fibra_max=req.get("fibra_max", 50),
                calc_max=req.get("calc_max", 2500),
                hierro_max=req.get("hierro_max", 45),
                vit_a_max_ui=req.get("vit_a_max_ui", 10000),
                vit_c_max=req.get("vit_c_max", 2000),
                zinc_min=req.get("zinc_min", 0),
                zinc_max=req.get("zinc_max", 40),
                yodo_min=req.get("yodo_min", 0),
                yodo_max=req.get("yodo_max", 600),
                selenio_min=req.get("selenio_min", 0),
                selenio_max=req.get("selenio_max", 300),
        ),
        costo_diario=round(resultado.costo_total, 0),
        costo_mensual=round(resultado.costo_mensual, 0),
        fuente_precios=resultado.fuente_precios,
    )


# ─── Modelos para modo familia ────────────────────────────────────────────────

class MiembroFamilia(BaseModel):
    nombre:       str = Field(..., description="Nombre o apodo del miembro")
    rango_etario: str = Field(..., description="Clave del rango etario OMS")
    filtros:      FiltrosRequest = Field(default_factory=FiltrosRequest)


class FamiliaRequest(BaseModel):
    provincia_codigo: Optional[str] = None
    miembros:         list[MiembroFamilia] = Field(..., min_length=1, max_length=10)


class AlimentoFamiliaItem(BaseModel):
    nombre:      str
    grupo:       str
    gramos_total: float
    costo_total:  float


class ResumenMiembro(BaseModel):
    nombre:        str
    rango_label:   str
    costo_diario:  float
    energia_kcal:  float
    proteinas_g:   float
    exito:         bool
    mensaje:       str


class FamiliaResponse(BaseModel):
    exito:            bool
    mensaje:          str
    provincia:        str
    n_miembros:       int
    miembros:         list[ResumenMiembro]
    lista_compras:    list[AlimentoFamiliaItem]
    costo_diario_total:  float
    costo_mensual_total: float
    fuente_precios:   str


@app.post("/api/plan/familia", response_model=FamiliaResponse, tags=["Optimización"])
def calcular_plan_familia(body: FamiliaRequest):
    """
    Calcula el plan nutricional para toda la familia y devuelve
    una lista de compras consolidada con cantidades sumadas.
    """
    # Validar rangos etarios
    for m in body.miembros:
        if m.rango_etario not in WHO_REQUIREMENTS:
            raise HTTPException(
                status_code=400,
                detail=f"Rango etario '{m.rango_etario}' no válido para {m.nombre}"
            )

    # Obtener precios
    try:
        if body.provincia_codigo:
            df_precios = sepa_cache.get_precios(provincia_codigo=body.provincia_codigo)
            nombre_provincia = PROVINCIAS.get(body.provincia_codigo, body.provincia_codigo)
        else:
            df_precios = _precios_referencia()
            nombre_provincia = "Nacional"
    except Exception:
        df_precios = _precios_referencia()
        nombre_provincia = "Nacional (fallback)"

    df_base = aplicar_precios(DF_BASE.copy(), df_precios)

    # Calcular plan para cada miembro
    resumenes  = []
    planes     = []
    fuente     = "referencia_local"

    for miembro in body.miembros:
        req     = WHO_REQUIREMENTS[miembro.rango_etario]
        filtros = FiltrosDieta(
            celiaco=miembro.filtros.celiaco,
            sin_lactosa=miembro.filtros.sin_lactosa,
            vegetariano=miembro.filtros.vegetariano,
            vegano=miembro.filtros.vegano,
            alergenos=miembro.filtros.alergenos,
        )

        try:
            resultado = optimizar_dieta(df_base, req, filtros)
        except Exception as e:
            resumenes.append(ResumenMiembro(
                nombre=miembro.nombre,
                rango_label=req["label"],
                costo_diario=0,
                energia_kcal=0,
                proteinas_g=0,
                exito=False,
                mensaje=str(e),
            ))
            continue

        if resultado.exito:
            fuente = resultado.fuente_precios
            ap = resultado.aportes
            resumenes.append(ResumenMiembro(
                nombre=miembro.nombre,
                rango_label=req["label"],
                costo_diario=round(resultado.costo_total, 0),
                energia_kcal=round(ap["energia_kcal"], 0),
                proteinas_g=round(ap["proteinas_g"], 1),
                exito=True,
                mensaje="OK",
            ))
            planes.append(resultado.alimentos)
        else:
            resumenes.append(ResumenMiembro(
                nombre=miembro.nombre,
                rango_label=req["label"],
                costo_diario=0,
                energia_kcal=0,
                proteinas_g=0,
                exito=False,
                mensaje=resultado.mensaje,
            ))

    if not planes:
        return FamiliaResponse(
            exito=False,
            mensaje="No se pudo calcular el plan para ningún miembro.",
            provincia=nombre_provincia,
            n_miembros=len(body.miembros),
            miembros=resumenes,
            lista_compras=[],
            costo_diario_total=0,
            costo_mensual_total=0,
            fuente_precios="error",
        )

    # Consolidar lista de compras — sumar gramos y costo por alimento
    import pandas as _pd
    df_consolidado = _pd.concat(planes, ignore_index=True)
    df_agrupado = (
        df_consolidado
        .groupby(['NOMBRE_COMPLETO', 'GRUPO'], as_index=False)
        .agg({'GRAMOS': 'sum', 'COSTO': 'sum'})
        .sort_values(['GRUPO', 'GRAMOS'], ascending=[True, False])
    )

    lista_compras = [
        AlimentoFamiliaItem(
            nombre=row['NOMBRE_COMPLETO'],
            grupo=row['GRUPO'],
            gramos_total=round(row['GRAMOS'], 0),
            costo_total=round(row['COSTO'], 0),
        )
        for _, row in df_agrupado.iterrows()
    ]

    costo_diario_total  = sum(r.costo_diario for r in resumenes)
    costo_mensual_total = costo_diario_total * 30

    return FamiliaResponse(
        exito=True,
        mensaje=f"Plan calculado para {len(planes)} miembro(s)",
        provincia=nombre_provincia,
        n_miembros=len(body.miembros),
        miembros=resumenes,
        lista_compras=lista_compras,
        costo_diario_total=round(costo_diario_total, 0),
        costo_mensual_total=round(costo_mensual_total, 0),
        fuente_precios=fuente,
    )
import pandas as _pd


# ─── Analizador inverso ───────────────────────────────────────────────────────

@app.get("/api/alimentos", tags=["Analizador"])
def get_alimentos(q: str = ""):
    """
    Lista de alimentos disponibles para el analizador inverso.
    Filtra por nombre si se pasa el parámetro q.
    """
    df = DF_BASE[DF_BASE['DISPONIBLE'] == True].copy()
    if q and len(q) >= 2:
        mask = df['NOMBRE_COMPLETO'].str.lower().str.contains(
            q.lower(), na=False)
        df = df[mask]
    return {
        "alimentos": [
            {
                "nombre":          row["ALIMENTO"],
                "nombre_completo": row["NOMBRE_COMPLETO"],
                "grupo":           row["GRUPO"],
                "cal_100g":        round(row["CAL"], 1) if not _pd.isna(row["CAL"]) else 0,
            }
            for _, row in df.head(80).iterrows()
        ]
    }


class AlimentoAnalisis(BaseModel):
    nombre:          str
    nombre_completo: Optional[str] = None
    gramos:          float = Field(..., gt=0)


class AnalisisRequest(BaseModel):
    rango_etario: str
    alimentos:    list[AlimentoAnalisis] = Field(..., min_length=1)


class ItemAnalisis(BaseModel):
    nombre:      str
    grupo:       str
    gramos:      float
    aporte_cal:  float
    aporte_pr:   float
    aporte_gr:   float
    aporte_hc:   float
    aporte_ca:   float
    aporte_fe:   float
    aporte_vitc: float


class ComparacionNutriente(BaseModel):
    nutriente:  str
    unidad:     str
    aporte:     float
    minimo:     float
    maximo:     Optional[float]
    pct:        float
    estado:     str   # 'ok' | 'bajo' | 'exceso'


class AnalisisResponse(BaseModel):
    exito:         bool
    mensaje:       str
    rango_label:   str
    n_alimentos:   int
    gramos_total:  float
    costo_total:   Optional[float]
    items:         list[ItemAnalisis]
    aportes:       AportesNutricionales
    req_oms:       ReqOMS
    comparacion:   list[ComparacionNutriente]


@app.post("/api/analizar", response_model=AnalisisResponse, tags=["Analizador"])
def analizar_dieta(body: AnalisisRequest):
    """
    Analiza una lista de alimentos con sus gramos y devuelve
    el perfil nutricional comparado con los requerimientos OMS.
    """
    if body.rango_etario not in WHO_REQUIREMENTS:
        raise HTTPException(status_code=400,
            detail=f"Rango etario '{body.rango_etario}' no válido.")

    req = WHO_REQUIREMENTS[body.rango_etario]
    df  = DF_BASE[DF_BASE['DISPONIBLE'] == True].copy()

    # Aplicar precios de referencia para costos aproximados
    from data.sepa_client import _precios_referencia, aplicar_precios
    df = aplicar_precios(df, _precios_referencia())

    items_out  = []
    aportes_acc = {k: 0.0 for k in [
        'energia_kcal','proteinas_g','grasas_g','hc_g','calcio_mg',
        'hierro_mg','vit_a_ui','vit_c_mg','vit_b1_mg','vit_b2_mg',
        'fibra_g','zinc_mg','yodo_ug','selenio_ug','gramos_total',
    ]}
    costo_total = 0.0
    no_encontrados = []

    for alim in body.alimentos:
        # Buscar en tabla por nombre exacto o parcial
        mask = df['ALIMENTO'].str.lower() == alim.nombre.lower()
        if not mask.any():
            mask = df['ALIMENTO'].str.lower().str.contains(
                alim.nombre.lower(), na=False)
        if not mask.any():
            no_encontrados.append(alim.nombre)
            continue

        row = df[mask].iloc[0]
        g   = alim.gramos

        def get(col):
            v = row.get(col, 0)
            return float(v) if not _pd.isna(v) else 0.0

        aporte_cal  = get('CAL_g')  * g
        aporte_pr   = get('PR_g')   * g
        aporte_gr   = get('GR_g')   * g
        aporte_hc   = get('HC_g')   * g
        aporte_ca   = get('CA_g')   * g
        aporte_fe   = get('FE_g')   * g
        aporte_va   = get('VIT_A_g')  * g
        aporte_vc   = get('VIT_C_g')  * g
        aporte_vb1  = get('VIT_B1_g') * g
        aporte_vb2  = get('VIT_B2_g') * g
        aporte_fib  = get('FIBRA_g') * g
        aporte_zn   = get('ZINC_g')  * g
        aporte_yo   = get('YODO_g')  * g if 'YODO_g' in df.columns else 0
        aporte_se   = get('SELENIO_g') * g if 'SELENIO_g' in df.columns else 0
        precio_g    = get('PRECIO_g')
        costo_item  = precio_g * g if precio_g > 0 else 0

        aportes_acc['energia_kcal']  += aporte_cal
        aportes_acc['proteinas_g']   += aporte_pr
        aportes_acc['grasas_g']      += aporte_gr
        aportes_acc['hc_g']          += aporte_hc
        aportes_acc['calcio_mg']     += aporte_ca
        aportes_acc['hierro_mg']     += aporte_fe
        aportes_acc['vit_a_ui']      += aporte_va
        aportes_acc['vit_c_mg']      += aporte_vc
        aportes_acc['vit_b1_mg']     += aporte_vb1
        aportes_acc['vit_b2_mg']     += aporte_vb2
        aportes_acc['fibra_g']       += aporte_fib
        aportes_acc['zinc_mg']       += aporte_zn
        aportes_acc['yodo_ug']       += aporte_yo
        aportes_acc['selenio_ug']    += aporte_se
        aportes_acc['gramos_total']  += g
        costo_total += costo_item

        items_out.append(ItemAnalisis(
            nombre     = row['NOMBRE_COMPLETO'],
            grupo      = row['GRUPO'],
            gramos     = round(g, 1),
            aporte_cal = round(aporte_cal, 1),
            aporte_pr  = round(aporte_pr, 1),
            aporte_gr  = round(aporte_gr, 1),
            aporte_hc  = round(aporte_hc, 1),
            aporte_ca  = round(aporte_ca, 1),
            aporte_fe  = round(aporte_fe, 2),
            aporte_vitc = round(aporte_vc, 1),
        ))

    if not items_out:
        raise HTTPException(status_code=404,
            detail=f"No se encontraron los alimentos: {no_encontrados}")

    # Comparación vs OMS
    def estado(val, minimo, maximo=None):
        if minimo > 0 and val / minimo < 0.9:  return 'bajo'
        if maximo and val > maximo * 1.1:       return 'exceso'
        return 'ok'

    def pct(val, minimo):
        return round(val / minimo * 100, 1) if minimo > 0 else 100.0

    ap = aportes_acc
    comparacion = [
        ComparacionNutriente(nutriente='Energía',      unidad='kcal', aporte=round(ap['energia_kcal'],1),  minimo=req['energia_min'],  maximo=req['energia_max'],  pct=pct(ap['energia_kcal'],req['energia_min']),  estado=estado(ap['energia_kcal'],req['energia_min'],req['energia_max'])),
        ComparacionNutriente(nutriente='Proteínas',    unidad='g',    aporte=round(ap['proteinas_g'],1),   minimo=req['proteinas_min'],maximo=None,                pct=pct(ap['proteinas_g'],req['proteinas_min']), estado=estado(ap['proteinas_g'],req['proteinas_min'])),
        ComparacionNutriente(nutriente='Grasas',       unidad='g',    aporte=round(ap['grasas_g'],1),      minimo=req['grasas_min'],   maximo=req['grasas_max'],   pct=pct(ap['grasas_g'],req['grasas_min']),       estado=estado(ap['grasas_g'],req['grasas_min'],req['grasas_max'])),
        ComparacionNutriente(nutriente='Carbohidratos',unidad='g',    aporte=round(ap['hc_g'],1),          minimo=req['hc_min'],       maximo=None,                pct=pct(ap['hc_g'],req['hc_min']),               estado=estado(ap['hc_g'],req['hc_min'])),
        ComparacionNutriente(nutriente='Fibra',        unidad='g',    aporte=round(ap['fibra_g'],1),       minimo=req.get('fibra_min',25), maximo=None,            pct=pct(ap['fibra_g'],req.get('fibra_min',25)),  estado=estado(ap['fibra_g'],req.get('fibra_min',25))),
        ComparacionNutriente(nutriente='Calcio',       unidad='mg',   aporte=round(ap['calcio_mg'],1),     minimo=req['calc_min'],     maximo=None,                pct=pct(ap['calcio_mg'],req['calc_min']),         estado=estado(ap['calcio_mg'],req['calc_min'])),
        ComparacionNutriente(nutriente='Hierro',       unidad='mg',   aporte=round(ap['hierro_mg'],2),     minimo=req['hierro_min'],   maximo=None,                pct=pct(ap['hierro_mg'],req['hierro_min']),       estado=estado(ap['hierro_mg'],req['hierro_min'])),
        ComparacionNutriente(nutriente='Vitamina A',   unidad='UI',   aporte=round(ap['vit_a_ui'],0),      minimo=req['vit_a_min_ui'], maximo=None,                pct=pct(ap['vit_a_ui'],req['vit_a_min_ui']),     estado=estado(ap['vit_a_ui'],req['vit_a_min_ui'])),
        ComparacionNutriente(nutriente='Vitamina C',   unidad='mg',   aporte=round(ap['vit_c_mg'],1),      minimo=req['vit_c_min'],    maximo=None,                pct=pct(ap['vit_c_mg'],req['vit_c_min']),         estado=estado(ap['vit_c_mg'],req['vit_c_min'])),
        ComparacionNutriente(nutriente='Zinc',         unidad='mg',   aporte=round(ap['zinc_mg'],2),       minimo=req.get('zinc_min',8), maximo=None,              pct=pct(ap['zinc_mg'],req.get('zinc_min',8)),    estado=estado(ap['zinc_mg'],req.get('zinc_min',8))),
    ]

    mensaje = "Análisis completado"
    if no_encontrados:
        mensaje += f". No encontrados: {', '.join(no_encontrados)}"

    return AnalisisResponse(
        exito        = True,
        mensaje      = mensaje,
        rango_label  = req['label'],
        n_alimentos  = len(items_out),
        gramos_total = round(ap['gramos_total'], 0),
        costo_total  = round(costo_total, 0) if costo_total > 0 else None,
        items        = items_out,
        aportes      = AportesNutricionales(
            energia_kcal = round(ap['energia_kcal'],1),
            proteinas_g  = round(ap['proteinas_g'],1),
            grasas_g     = round(ap['grasas_g'],1),
            hc_g         = round(ap['hc_g'],1),
            calcio_mg    = round(ap['calcio_mg'],1),
            hierro_mg    = round(ap['hierro_mg'],2),
            vit_a_ui     = round(ap['vit_a_ui'],0),
            vit_c_mg     = round(ap['vit_c_mg'],1),
            vit_b1_mg    = round(ap['vit_b1_mg'],2),
            vit_b2_mg    = round(ap['vit_b2_mg'],2),
            fibra_g      = round(ap['fibra_g'],1),
            gramos_total = round(ap['gramos_total'],0),
            zinc_mg      = round(ap['zinc_mg'],2),
            yodo_ug      = round(ap['yodo_ug'],1),
            selenio_ug   = round(ap['selenio_ug'],1),
        ),
        req_oms = ReqOMS(
            energia_min=req['energia_min'], energia_max=req['energia_max'],
            proteinas_min=req['proteinas_min'],
            grasas_min=req['grasas_min'], grasas_max=req['grasas_max'],
            hc_min=req['hc_min'], hc_max=req.get('hc_max', req['hc_min']*4),
            calc_min=req['calc_min'],     calc_max=req.get('calc_max', 2500),
            hierro_min=req['hierro_min'], hierro_max=req.get('hierro_max', 45),
            vit_a_min_ui=req['vit_a_min_ui'], vit_a_max_ui=req.get('vit_a_max_ui', 10000),
            vit_c_min=req['vit_c_min'],   vit_c_max=req.get('vit_c_max', 2000),
            vit_b1_min=req['vit_b1_min'], vit_b2_min=req['vit_b2_min'],
            fibra_min=req.get('fibra_min',0), fibra_max=req.get('fibra_max', 50),
            zinc_min=req.get('zinc_min',0),   zinc_max=req.get('zinc_max', 40),
            yodo_min=req.get('yodo_min',0),   yodo_max=req.get('yodo_max', 600),
            selenio_min=req.get('selenio_min',0), selenio_max=req.get('selenio_max', 300),
        ),
        comparacion = comparacion,
    )

@app.get("/api/status", tags=["Estado"])
def get_status():
    """Estado del servidor y del caché de precios SEPA."""
    return {
        "status":         "ok",
        "sepa_status":    sepa_cache.status,
        "sepa_mensaje":   sepa_cache.mensaje,
        "sepa_listo":     sepa_cache.listo,
        "fuente_precios": sepa_cache.fuente(),
    }

# ─── Modo "¿Con qué tengo?" ──────────────────────────────────────────────────

class CompletarRequest(BaseModel):
    rango_etario: str
    provincia_codigo: Optional[str] = None
    alimentos: list[AlimentoAnalisis] = Field(..., min_length=1)
    filtros: Optional[FiltrosDieta] = None


class CompletarResponse(BaseModel):
    exito:           bool
    mensaje:         str
    rango_label:     str
    # Lo que ya tenés
    ya_tienes:       list[ItemAnalisis]
    aportes_actuales: AportesNutricionales
    # Lo que necesitás agregar
    agregar:         list[dict]
    aportes_adicionales: AportesNutricionales
    # Combinado
    aportes_totales: AportesNutricionales
    req_oms:         ReqOMS
    costo_adicional: float
    costo_total_dia: float
    nutrientes_cubiertos: list[str]
    nutrientes_faltantes:  list[str]


@app.post("/api/completar", response_model=CompletarResponse, tags=["Completar"])
def completar_dieta(body: CompletarRequest):
    """
    Analiza los alimentos que el usuario ya tiene y sugiere
    qué agregar para cubrir los requerimientos OMS completos.
    """
    if body.rango_etario not in WHO_REQUIREMENTS:
        raise HTTPException(status_code=400,
            detail=f"Rango etario '{body.rango_etario}' no válido.")

    req    = WHO_REQUIREMENTS[body.rango_etario]
    df_base = DF_BASE[DF_BASE['DISPONIBLE'] == True].copy()
    df_base = aplicar_precios(df_base, sepa_cache.get_precios(
        provincia_codigo=body.provincia_codigo if body.provincia_codigo else None
    ))

    # ── Calcular aportes de lo que ya tiene ──────────────────────────────────
    def get_val(row, col):
        v = row.get(col, 0)
        return float(v) if not _pd.isna(v) else 0.0

    aportes_acc = {k: 0.0 for k in [
        'energia_kcal','proteinas_g','grasas_g','hc_g','calcio_mg',
        'hierro_mg','vit_a_ui','vit_c_mg','vit_b1_mg','vit_b2_mg',
        'fibra_g','zinc_mg','yodo_ug','selenio_ug','gramos_total',
    ]}

    items_tiene = []
    for alim in body.alimentos:
        mask = df_base['ALIMENTO'].str.lower() == alim.nombre.lower()
        if not mask.any():
            mask = df_base['ALIMENTO'].str.lower().str.contains(alim.nombre.lower(), na=False)
        if not mask.any():
            continue
        row = df_base[mask].iloc[0]
        g   = alim.gramos

        aportes_acc['energia_kcal']  += get_val(row,'CAL_g')  * g
        aportes_acc['proteinas_g']   += get_val(row,'PR_g')   * g
        aportes_acc['grasas_g']      += get_val(row,'GR_g')   * g
        aportes_acc['hc_g']          += get_val(row,'HC_g')   * g
        aportes_acc['calcio_mg']     += get_val(row,'CA_g')   * g
        aportes_acc['hierro_mg']     += get_val(row,'FE_g')   * g
        aportes_acc['vit_a_ui']      += get_val(row,'VIT_A_g')  * g
        aportes_acc['vit_c_mg']      += get_val(row,'VIT_C_g')  * g
        aportes_acc['vit_b1_mg']     += get_val(row,'VIT_B1_g') * g
        aportes_acc['vit_b2_mg']     += get_val(row,'VIT_B2_g') * g
        aportes_acc['fibra_g']       += get_val(row,'FIBRA_g')  * g
        aportes_acc['zinc_mg']       += get_val(row,'ZINC_g')   * g
        aportes_acc['yodo_ug']       += get_val(row,'YODO_g')   * g if 'YODO_g' in df_base.columns else 0
        aportes_acc['selenio_ug']    += get_val(row,'SELENIO_g')* g if 'SELENIO_g' in df_base.columns else 0
        aportes_acc['gramos_total']  += g

        items_tiene.append(ItemAnalisis(
            nombre=row['NOMBRE_COMPLETO'], grupo=row['GRUPO'], gramos=round(g,1),
            aporte_cal=round(get_val(row,'CAL_g')*g,1),
            aporte_pr=round(get_val(row,'PR_g')*g,1),
            aporte_gr=round(get_val(row,'GR_g')*g,1),
            aporte_hc=round(get_val(row,'HC_g')*g,1),
            aporte_ca=round(get_val(row,'CA_g')*g,1),
            aporte_fe=round(get_val(row,'FE_g')*g,2),
            aporte_vitc=round(get_val(row,'VIT_C_g')*g,1),
        ))

    # ── Calcular requerimientos restantes ────────────────────────────────────
    def restante(actual, minimo):
        return max(0.0, minimo - actual)

    # Para los máximos usamos el original — NO restamos lo ya consumido.
    # Si restáramos, un nutriente ya cubierto al 100% daría max=0,
    # lo que haría al LP infactible (no puede agregar nada sin violar el techo).
    req_restante = {
        # Mínimos: solo lo que falta cubrir
        'energia_min':   restante(aportes_acc['energia_kcal'], req['energia_min']),
        'energia_max':   req['energia_max'],   # máximo original
        'proteinas_min': restante(aportes_acc['proteinas_g'],  req['proteinas_min']),
        'grasas_min':    restante(aportes_acc['grasas_g'],     req['grasas_min']),
        'grasas_max':    req['grasas_max'],
        'hc_min':        restante(aportes_acc['hc_g'],         req['hc_min']),
        'hc_max':        req.get('hc_max', req['hc_min'] * 4),
        'calc_min':      restante(aportes_acc['calcio_mg'],    req['calc_min']),
        'hierro_min':    restante(aportes_acc['hierro_mg'],    req['hierro_min']),
        'vit_a_min_ui':  restante(aportes_acc['vit_a_ui'],     req['vit_a_min_ui']),
        'vit_c_min':     restante(aportes_acc['vit_c_mg'],     req['vit_c_min']),
        'vit_b1_min':    restante(aportes_acc['vit_b1_mg'],    req['vit_b1_min']),
        'vit_b2_min':    restante(aportes_acc['vit_b2_mg'],    req['vit_b2_min']),
        'fibra_min':     restante(aportes_acc['fibra_g'],      req.get('fibra_min', 25)),
        'zinc_min':      restante(aportes_acc['zinc_mg'],      req.get('zinc_min', 8)),
        'yodo_min':      restante(aportes_acc['yodo_ug'],      req.get('yodo_min', 150)),
        'selenio_min':   restante(aportes_acc['selenio_ug'],   req.get('selenio_min', 55)),
        # UL heredados del rango original
        'vit_a_max_ui':  req.get('vit_a_max_ui', 10000),
        'hierro_max':    req.get('hierro_max', 45),
        'selenio_max':   req.get('selenio_max', 300),
        'zinc_max':      req.get('zinc_max', 40),
    }
    # Propagar metadatos que el optimizador necesita
    for k in ['label', 'edad_min', 'edad_max', 'sexo']:
        if k in req:
            req_restante[k] = req[k]

    # ── Correr LP con requerimientos restantes ────────────────────────────────
    # Excluir alimentos que el usuario ya ingresó
    nombres_tiene = {a.nombre.lower() for a in body.alimentos}
    df_lp = df_base[~df_base['ALIMENTO'].str.lower().isin(nombres_tiene)].copy()

    filtros = body.filtros or FiltrosDieta()
    from engine.optimizer import optimizar_dieta, _aplicar_filtros as aplicar_filtros
    df_lp = aplicar_filtros(df_lp, filtros)

    res_lp = optimizar_dieta(df_lp, req_restante)

    # ── Construir respuesta ───────────────────────────────────────────────────
    agregar_lista = []
    ap2 = {'energia_kcal':0,'proteinas_g':0,'grasas_g':0,'hc_g':0,
           'calcio_mg':0,'hierro_mg':0,'vit_a_ui':0,'vit_c_mg':0,
           'vit_b1_mg':0,'vit_b2_mg':0,'fibra_g':0,'zinc_mg':0,
           'yodo_ug':0,'selenio_ug':0,'gramos_total':0}

    if res_lp.exito:
        for _, row in res_lp.alimentos.iterrows():
            gramos = float(row['GRAMOS'])
            # El optimizador devuelve columna COSTO (ya calculada)
            costo  = round(float(row.get('COSTO', 0) or 0), 1)
            agregar_lista.append({
                'nombre':   row.get('NOMBRE_COMPLETO', row.get('ALIMENTO','')),
                'grupo':    row.get('GRUPO',''),
                'gramos':   round(gramos, 1),
                'costo_ars': costo,
            })
        a = res_lp.aportes
        ap2 = {k: a.get(k,0) for k in ap2}

    def sumar(a, b):
        return {k: round(a.get(k,0) + b.get(k,0), 2) for k in a}

    ap_total = sumar(aportes_acc, ap2)

    # Nutrientes cubiertos/faltantes
    cubrir = {
        'Energía':    aportes_acc['energia_kcal'] >= req['energia_min'] * 0.9,
        'Proteínas':  aportes_acc['proteinas_g']  >= req['proteinas_min'] * 0.9,
        'Grasas':     aportes_acc['grasas_g']     >= req['grasas_min'] * 0.9,
        'Carbohidratos': aportes_acc['hc_g']      >= req['hc_min'] * 0.9,
        'Calcio':     aportes_acc['calcio_mg']    >= req['calc_min'] * 0.9,
        'Hierro':     aportes_acc['hierro_mg']    >= req['hierro_min'] * 0.9,
        'Vitamina A': aportes_acc['vit_a_ui']     >= req['vit_a_min_ui'] * 0.9,
        'Vitamina C': aportes_acc['vit_c_mg']     >= req['vit_c_min'] * 0.9,
    }

    def make_aportes(a):
        return AportesNutricionales(
            energia_kcal=round(a['energia_kcal'],1),
            proteinas_g=round(a['proteinas_g'],1),
            grasas_g=round(a['grasas_g'],1),
            hc_g=round(a['hc_g'],1),
            calcio_mg=round(a['calcio_mg'],1),
            hierro_mg=round(a['hierro_mg'],2),
            vit_a_ui=round(a['vit_a_ui'],0),
            vit_c_mg=round(a['vit_c_mg'],1),
            vit_b1_mg=round(a['vit_b1_mg'],2),
            vit_b2_mg=round(a['vit_b2_mg'],2),
            fibra_g=round(a['fibra_g'],1),
            gramos_total=round(a['gramos_total'],0),
            zinc_mg=round(a['zinc_mg'],2),
            yodo_ug=round(a['yodo_ug'],1),
            selenio_ug=round(a['selenio_ug'],1),
        )

    costo_adicional = sum(a.get('costo_ars',0) for a in agregar_lista)
    costo_tiene     = sum(
        float(df_base[df_base['ALIMENTO'].str.lower()==al.nombre.lower()]['PRECIO_g'].iloc[0]) * al.gramos
        if (df_base['ALIMENTO'].str.lower()==al.nombre.lower()).any() else 0
        for al in body.alimentos
    )

    req_oms_obj = ReqOMS(
        energia_min=req['energia_min'], energia_max=req['energia_max'],
        proteinas_min=req['proteinas_min'],
        grasas_min=req['grasas_min'], grasas_max=req['grasas_max'],
        hc_min=req['hc_min'], hc_max=req.get('hc_max', req['hc_min']*4),
        calc_min=req['calc_min'],   calc_max=req.get('calc_max',2500),
        hierro_min=req['hierro_min'], hierro_max=req.get('hierro_max',45),
        vit_a_min_ui=req['vit_a_min_ui'], vit_a_max_ui=req.get('vit_a_max_ui',10000),
        vit_c_min=req['vit_c_min'],   vit_c_max=req.get('vit_c_max',2000),
        vit_b1_min=req['vit_b1_min'], vit_b2_min=req['vit_b2_min'],
        fibra_min=req.get('fibra_min',0), fibra_max=req.get('fibra_max',50),
        zinc_min=req.get('zinc_min',0),   zinc_max=req.get('zinc_max',40),
        yodo_min=req.get('yodo_min',0),   yodo_max=req.get('yodo_max',600),
        selenio_min=req.get('selenio_min',0), selenio_max=req.get('selenio_max',300),
    )

    return CompletarResponse(
        exito=True,
        mensaje="Análisis completado" if res_lp.exito else
                "No se encontró solución completa con lo disponible",
        rango_label=req.get('label',''),
        ya_tienes=items_tiene,
        aportes_actuales=make_aportes(aportes_acc),
        agregar=agregar_lista,
        aportes_adicionales=make_aportes(ap2),
        aportes_totales=make_aportes(ap_total),
        req_oms=req_oms_obj,
        costo_adicional=round(costo_adicional,0),
        costo_total_dia=round(costo_tiene + costo_adicional,0),
        nutrientes_cubiertos=[k for k,v in cubrir.items() if v],
        nutrientes_faltantes=[k for k,v in cubrir.items() if not v],
    )
