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
from data.sepa_client import obtener_precios_sepa, aplicar_precios, _precios_referencia, PROVINCIAS
from data.who_requirements import WHO_REQUIREMENTS, WHO_GROUPS_UI
from engine.optimizer import optimizar_dieta, FiltrosDieta

# ─── Inicializar app ───────────────────────────────────────────────────────────
app = FastAPI(
    title="NutriPlan API",
    description="Planificador de dieta nutricional económica para Argentina — OMS",
    version="2.0.0",
)

# CORS: permite que el frontend React (cualquier origen) consulte la API
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
    fibra_min:    float
    zinc_min:     float
    yodo_min:     float
    selenio_min:  float


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

    # Obtener precios
    try:
        if body.provincia_codigo:
            df_precios = obtener_precios_sepa(provincia_codigo=body.provincia_codigo)
            nombre_provincia = PROVINCIAS.get(body.provincia_codigo, body.provincia_codigo)
        else:
            df_precios = _precios_referencia()
            nombre_provincia = "Nacional"
    except Exception:
        df_precios = _precios_referencia()
        nombre_provincia = "Nacional (fallback)"

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
                fibra_min=req.get("fibra_min", 0),
                zinc_min=req.get("zinc_min", 0),
                yodo_min=req.get("yodo_min", 0),
                selenio_min=req.get("selenio_min", 0),
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
                fibra_min=req.get("fibra_min", 0),
                zinc_min=req.get("zinc_min", 0),
                yodo_min=req.get("yodo_min", 0),
                selenio_min=req.get("selenio_min", 0),
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
            df_precios = obtener_precios_sepa(provincia_codigo=body.provincia_codigo)
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
