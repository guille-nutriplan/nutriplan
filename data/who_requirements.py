"""
Requerimientos nutricionales diarios según OMS/FAO/UNU 2004
Fuente: Human Vitamin and Mineral Requirements (FAO/WHO 2001)
        Energy and Protein Requirements (FAO/WHO/UNU 2004)
        ANMAT - Guías Alimentarias para la Población Argentina

Unidades:
  energia  : kcal/día
  proteinas: g/día
  grasas   : % energía → convertido a g (usando 9 kcal/g)
  hc       : g/día
  calc     : mg/día  (Calcio)
  hierro   : mg/día  (Fe)
  vit_a    : µg RE/día (Vitamina A, Retinol Equivalente)
             Nota: la tabla usa UI (1 µg RE ≈ 3.33 UI para retinol)
  vit_c    : mg/día  (Vitamina C)
  vit_b1   : mg/día  (Tiamina) — tabla en mcg, convertir
  vit_b2   : mg/día  (Riboflavina) — tabla en mcg, convertir

Para LP:
  *_min → restricción >= (lower bound)
  *_max → restricción <= (upper bound)
  Proteínas y micronutrientes: solo mínimo
  Grasas: mínimo Y máximo
  Energía: mínimo (y máximo laxo = +15% para no sobrealimentar)
"""

WHO_REQUIREMENTS = {
    # ─── LACTANTES ────────────────────────────────────────────────────────────
    '0-6m': {
        '_rango_key': '0-6m',
        'zinc_min': 2.0,
        'yodo_min': 90,
        'selenio_min': 15,
        'fibra_min': 0,
        'label': 'Lactante 0-6 meses',
        'nota': 'Alimentación exclusiva con leche materna',
        'energia_min': 550, 'energia_max': 650,
        'proteinas_min': 9.1,
        'grasas_min': 25, 'grasas_max': 35,    # 40-55% de la energía
        'hc_min': 60,
        'calc_min': 300,
        'hierro_min': 0.27,
        'vit_a_min_ui': 1000,   # 300 µg RE → ~1000 UI
        'vit_c_min': 25,
        'vit_b1_min': 0.2,
        'vit_b2_min': 0.3,
    },
    '6-12m': {
        '_rango_key': '6-12m',
        'zinc_min': 3.0,
        'yodo_min': 90,
        'selenio_min': 20,
        'fibra_min': 0,
        'label': 'Lactante 6-12 meses',
        'nota': 'Alimentación complementaria',
        'energia_min': 700, 'energia_max': 900,
        'proteinas_min': 13.5,
        'grasas_min': 28, 'grasas_max': 40,
        'hc_min': 95,
        'calc_min': 400,
        'hierro_min': 6.2,
        'vit_a_min_ui': 1167,   # 350 µg RE
        'vit_c_min': 25,
        'vit_b1_min': 0.3,
        'vit_b2_min': 0.4,
    },
    # ─── NIÑOS ────────────────────────────────────────────────────────────────
    '1-3': {
        '_rango_key': '1-3',
        'fibra_min': 14,
        'zinc_min': 3.0,
        'yodo_min': 90,
        'selenio_min': 20,
        'label': 'Niño/a 1-3 años',
        'energia_min': 1125, 'energia_max': 1350,
        'proteinas_min': 13,
        'grasas_min': 30, 'grasas_max': 40,
        'hc_min': 130,
        'calc_min': 500,
        'hierro_min': 6.2,
        'vit_a_min_ui': 1000,   # 300 µg RE → ~1000 UI (retinol)
        'vit_c_min': 30,
        'vit_b1_min': 0.5,
        'vit_b2_min': 0.5,
    },
    '4-6': {
        '_rango_key': '4-6',
        'zinc_min': 5.0,
        'yodo_min': 90,
        'selenio_min': 22,
        'fibra_min': 18,
        'label': 'Niño/a 4-6 años',
        'energia_min': 1550, 'energia_max': 1800,
        'proteinas_min': 19,
        'grasas_min': 35, 'grasas_max': 49,
        'hc_min': 130,
        'calc_min': 600,
        'hierro_min': 6.3,
        'vit_a_min_ui': 1167,   # 350 µg RE
        'vit_c_min': 30,
        'vit_b1_min': 0.6,
        'vit_b2_min': 0.6,
    },
    '7-9': {
        '_rango_key': '7-9',
        'fibra_min': 21,
        'zinc_min': 5.0,
        'yodo_min': 90,
        'selenio_min': 25,
        'label': 'Niño/a 7-9 años',
        'energia_min': 1850, 'energia_max': 2100,
        'proteinas_min': 24,
        'grasas_min': 41, 'grasas_max': 58,
        'hc_min': 130,
        'calc_min': 700,
        'hierro_min': 8.9,
        'vit_a_min_ui': 1333,   # 400 µg RE
        'vit_c_min': 35,
        'vit_b1_min': 0.9,
        'vit_b2_min': 0.9,
    },
    # ─── ADOLESCENTES ─────────────────────────────────────────────────────────
    '10-13M': {
        '_rango_key': '10-13M',
        'zinc_min': 8.0,
        'yodo_min': 120,
        'selenio_min': 35,
        'fibra_min': 25,
        'label': 'Varón 10-13 años',
        'energia_min': 2100, 'energia_max': 2400,
        'proteinas_min': 34,
        'grasas_min': 47, 'grasas_max': 67,
        'hc_min': 130,
        'calc_min': 1300,
        'hierro_min': 9.7,
        'vit_a_min_ui': 2000,   # 600 µg RE
        'vit_c_min': 40,
        'vit_b1_min': 1.2,
        'vit_b2_min': 1.2,
    },
    '10-13F': {
        '_rango_key': '10-13F',
        'fibra_min': 24,
        'zinc_min': 8.0,
        'yodo_min': 120,
        'selenio_min': 35,
        'label': 'Mujer 10-13 años',
        'energia_min': 2000, 'energia_max': 2300,
        'proteinas_min': 36,
        'grasas_min': 44, 'grasas_max': 64,
        'hc_min': 130,
        'calc_min': 1300,
        'hierro_min': 21.8,     # Inicio menarca
        'vit_a_min_ui': 2000,
        'vit_c_min': 40,
        'vit_b1_min': 1.1,
        'vit_b2_min': 1.0,
    },
    '14-17M': {
        '_rango_key': '14-17M',
        'zinc_min': 11.0,
        'yodo_min': 150,
        'selenio_min': 45,
        'fibra_min': 31,
        'label': 'Varón 14-17 años',
        'energia_min': 2650, 'energia_max': 3000,
        'proteinas_min': 49,
        'grasas_min': 59, 'grasas_max': 83,
        'hc_min': 130,
        'calc_min': 1300,
        'hierro_min': 12.5,
        'vit_a_min_ui': 2000,
        'vit_c_min': 40,
        'vit_b1_min': 1.4,
        'vit_b2_min': 1.4,
    },
    '14-17F': {
        '_rango_key': '14-17F',
        'zinc_min': 9.0,
        'yodo_min': 150,
        'selenio_min': 45,
        'fibra_min': 26,
        'label': 'Mujer 14-17 años',
        'energia_min': 2100, 'energia_max': 2400,
        'proteinas_min': 44,
        'grasas_min': 47, 'grasas_max': 67,
        'hc_min': 130,
        'calc_min': 1300,
        'hierro_min': 20.7,
        'vit_a_min_ui': 2000,
        'vit_c_min': 40,
        'vit_b1_min': 1.2,
        'vit_b2_min': 1.2,
    },
    # ─── ADULTOS ──────────────────────────────────────────────────────────────
    '18-29M': {
        '_rango_key': '18-29M',
        'fibra_min': 38,
        'zinc_min': 11.0,
        'yodo_min': 150,
        'selenio_min': 55,
        'label': 'Varón adulto 18-29 años',
        'energia_min': 2550, 'energia_max': 2900,
        'proteinas_min': 56,
        'grasas_min': 57, 'grasas_max': 85,
        'hc_min': 130,
        'calc_min': 1000,
        'hierro_min': 9.1,
        'vit_a_min_ui': 2000,
        'vit_c_min': 45,
        'vit_b1_min': 1.4,
        'vit_b2_min': 1.6,
    },
    '18-29F': {
        '_rango_key': '18-29F',
        'fibra_min': 25,
        'zinc_min': 8.0,
        'yodo_min': 150,
        'selenio_min': 55,
        'label': 'Mujer adulta 18-29 años',
        'energia_min': 2000, 'energia_max': 2300,
        'proteinas_min': 48,
        'grasas_min': 44, 'grasas_max': 67,
        'hc_min': 130,
        'calc_min': 1000,
        'hierro_min': 19.6,
        'vit_a_min_ui': 1667,   # 500 µg RE
        'vit_c_min': 45,
        'vit_b1_min': 1.1,
        'vit_b2_min': 1.1,
    },
    '30-59M': {
        '_rango_key': '30-59M',
        'fibra_min': 38,
        'zinc_min': 11.0,
        'yodo_min': 150,
        'selenio_min': 55,
        'label': 'Varón adulto 30-59 años',
        'energia_min': 2500, 'energia_max': 2875,
        'proteinas_min': 56,
        'grasas_min': 56, 'grasas_max': 83,
        'hc_min': 130,
        'calc_min': 1000,
        'hierro_min': 9.1,
        'vit_a_min_ui': 2000,
        'vit_c_min': 45,
        'vit_b1_min': 1.4,
        'vit_b2_min': 1.6,
    },
    '30-59F': {
        '_rango_key': '30-59F',
        'fibra_min': 25,
        'zinc_min': 8.0,
        'yodo_min': 150,
        'selenio_min': 55,
        'label': 'Mujer adulta 30-59 años',
        'energia_min': 1900, 'energia_max': 2185,
        'proteinas_min': 48,
        'grasas_min': 42, 'grasas_max': 63,
        'hc_min': 130,
        'calc_min': 1000,
        'hierro_min': 19.6,
        'vit_a_min_ui': 1667,
        'vit_c_min': 45,
        'vit_b1_min': 1.1,
        'vit_b2_min': 1.1,
    },
    # ─── ADULTOS MAYORES ──────────────────────────────────────────────────────
    '60+M': {
        '_rango_key': '60+M',
        'fibra_min': 30,
        'zinc_min': 11.0,
        'yodo_min': 150,
        'selenio_min': 55,
        'label': 'Varón adulto mayor 60+ años',
        'energia_min': 2000, 'energia_max': 2300,
        'proteinas_min': 56,
        'grasas_min': 44, 'grasas_max': 67,
        'hc_min': 130,
        'calc_min': 1200,
        'hierro_min': 9.1,
        'vit_a_min_ui': 2000,
        'vit_c_min': 45,
        'vit_b1_min': 1.4,
        'vit_b2_min': 1.6,
    },
    '60+F': {
        '_rango_key': '60+F',
        'fibra_min': 21,
        'zinc_min': 8.0,
        'yodo_min': 150,
        'selenio_min': 55,
        'label': 'Mujer adulta mayor 60+ años',
        'energia_min': 1700, 'energia_max': 1955,
        'proteinas_min': 48,
        'grasas_min': 38, 'grasas_max': 57,
        'hc_min': 130,
        'calc_min': 1200,
        'hierro_min': 9.1,     # Posmenopáusica
        'vit_a_min_ui': 1667,
        'vit_c_min': 45,
        'vit_b1_min': 1.1,
        'vit_b2_min': 1.1,
    },
    # ─── SITUACIONES ESPECIALES ───────────────────────────────────────────────
    'embarazada': {
        '_rango_key': 'embarazada',
        'fibra_min': 28,
        'zinc_min': 11.0,
        'yodo_min': 220,
        'selenio_min': 60,
        'label': 'Mujer embarazada',
        'nota': '+300 kcal sobre requerimiento base',
        'energia_min': 2340, 'energia_max': 2700,
        'proteinas_min': 58,
        'grasas_min': 52, 'grasas_max': 78,
        'hc_min': 175,         # Mayor demanda fetal
        'calc_min': 1200,
        'hierro_min': 27.0,    # Suplementación frecuentemente necesaria
        'vit_a_min_ui': 2333,  # 700 µg RE
        'vit_c_min': 55,
        'vit_b1_min': 1.5,
        'vit_b2_min': 1.4,
    },
    'lactante_madre': {
        '_rango_key': 'lactante_madre',
        'zinc_min': 12.0,
        'yodo_min': 290,
        'selenio_min': 70,
        'fibra_min': 29,
        'label': 'Mujer lactante',
        'nota': '+500 kcal sobre requerimiento base',
        'energia_min': 2640, 'energia_max': 3000,
        'proteinas_min': 63,
        'grasas_min': 59, 'grasas_max': 88,
        'hc_min': 210,
        'calc_min': 1000,
        'hierro_min': 10.0,
        'vit_a_min_ui': 2833,  # 850 µg RE
        'vit_c_min': 70,
        'vit_b1_min': 1.5,
        'vit_b2_min': 1.6,
    },
}

# Orden para mostrar en UI (agrupado)
WHO_GROUPS_UI = [
    {
        'grupo': 'Bebés',
        'rangos': ['0-6m', '6-12m']
    },
    {
        'grupo': 'Niños y Niñas',
        'rangos': ['1-3', '4-6', '7-9']
    },
    {
        'grupo': 'Adolescentes',
        'rangos': ['10-13M', '10-13F', '14-17M', '14-17F']
    },
    {
        'grupo': 'Adultos',
        'rangos': ['18-29M', '18-29F', '30-59M', '30-59F']
    },
    {
        'grupo': 'Adultos Mayores',
        'rangos': ['60+M', '60+F']
    },
    {
        'grupo': 'Situaciones Especiales',
        'rangos': ['embarazada', 'lactante_madre']
    },
]
