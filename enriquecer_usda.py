"""
enriquecer_usda.py
Script de enriquecimiento one-time: consulta USDA FoodData Central API
y obtiene valores reales de Zinc, Yodo y Selenio para cada alimento
de nuestra tabla de composición.

Resultado: data/enriquecimiento_usda.csv
  Columnas: ALIMENTO, ESTADO, GRUPO, ZINC_mg, YODO_ug, SELENIO_ug, FDC_ID, FDC_NOMBRE

Uso:
    python enriquecer_usda.py

Requiere FDC_API_KEY en .env o variable de entorno.
"""

import sys
import os
import time
import json
import requests
import pandas as pd
from pathlib import Path
from dotenv import load_dotenv

# Cargar .env
load_dotenv()
API_KEY = os.getenv('FDC_API_KEY', 'DEMO_KEY')
BASE_URL = 'https://api.nal.usda.gov/fdc/v1'

sys.path.insert(0, str(Path(__file__).parent))
from data.tabla_loader import cargar_tabla

# ─── IDs de nutrientes en USDA ────────────────────────────────────────────────
# https://fdc.nal.usda.gov/api-spec/fdc_api.html
NUTRIENT_IDS = {
    'ZINC':    1095,   # Zinc (mg)
    'YODO':    1100,   # Iodine (µg)
    'SELENIO': 1103,   # Selenium (µg)
}

# ─── Diccionario de traducción español → inglés para búsqueda ─────────────────
TRADUCCIONES = {
    'arroz':          'rice white',
    'fideos':         'pasta spaghetti',
    'macarrones':     'macaroni',
    'galletas':       'crackers',
    'pan':            'bread white',
    'avena':          'oats',
    'maiz':           'corn',
    'lenteja':        'lentils',
    'poroto':         'beans kidney',
    'garbanzo':       'chickpeas',
    'arvejas':        'peas green',
    'soja':           'soybean',
    'papa':           'potato',
    'patata':         'potato',
    'batata':         'sweet potato',
    'zapallo':        'pumpkin',
    'zanahoria':      'carrot',
    'tomate':         'tomato',
    'lechuga':        'lettuce',
    'espinaca':       'spinach',
    'acelga':         'chard swiss',
    'cebolla':        'onion',
    'ajo':            'garlic',
    'brocoli':        'broccoli',
    'manzana':        'apple',
    'naranja':        'orange',
    'banana':         'banana',
    'uva':            'grapes',
    'pera':           'pear',
    'durazno':        'peach',
    'frutilla':       'strawberry',
    'limon':          'lemon',
    'leche':          'milk whole',
    'yogur':          'yogurt plain',
    'queso':          'cheese cheddar',
    'ricota':         'ricotta',
    'manteca':        'butter',
    'crema':          'cream heavy',
    'huevo':          'egg whole',
    'aceite':         'oil vegetable',
    'azucar':         'sugar white',
    'miel':           'honey',
    'chocolate':      'chocolate milk',
    'almendra':       'almonds',
    'nuez':           'walnuts',
    'mani':           'peanuts',
    'pollo':          'chicken breast',
    'pechuga':        'chicken breast',
    'bife':           'beef sirloin',
    'carne':          'beef ground',
    'cerdo':          'pork loin',
    'cordero':        'lamb',
    'merluza':        'hake',
    'atun':           'tuna canned',
    'sardina':        'sardines canned',
    'salmon':         'salmon',
    'jamon':          'ham',
    'salchicha':      'frankfurter',
    'mortadela':      'bologna',
}

# ─── Caché de búsquedas ───────────────────────────────────────────────────────
CACHE_FILE = Path('data/cache/usda_cache.json')
CACHE_FILE.parent.mkdir(exist_ok=True)

def cargar_cache():
    if CACHE_FILE.exists():
        return json.loads(CACHE_FILE.read_text())
    return {}

def guardar_cache(cache):
    CACHE_FILE.write_text(json.dumps(cache, indent=2, ensure_ascii=False))


def traducir(alimento: str) -> str:
    """Traduce el nombre del alimento al inglés para búsqueda en USDA."""
    al = alimento.lower().strip()
    for es, en in TRADUCCIONES.items():
        if es in al:
            return en
    # Si no hay traducción, devolver el nombre en español
    # USDA tiene algunos alimentos en español también
    return al


def buscar_alimento_usda(query: str, cache: dict) -> dict | None:
    """
    Busca un alimento en USDA y retorna sus nutrientes.
    Usa caché para no repetir búsquedas.
    """
    if query in cache:
        return cache[query]

    try:
        # Búsqueda por texto — preferir Foundation Foods y SR Legacy
        resp = requests.get(
            f'{BASE_URL}/foods/search',
            params={
                'api_key':   API_KEY,
                'query':     query,
                'dataType':  'Foundation,SR Legacy',
                'pageSize':  3,
            },
            timeout=15
        )
        resp.raise_for_status()
        data = resp.json()

        if not data.get('foods'):
            cache[query] = None
            return None

        # Tomar el primer resultado
        food = data['foods'][0]
        fdc_id = food['fdcId']

        # Obtener detalle del alimento con todos los nutrientes
        resp2 = requests.get(
            f'{BASE_URL}/food/{fdc_id}',
            params={'api_key': API_KEY},
            timeout=15
        )
        resp2.raise_for_status()
        detalle = resp2.json()

        # Extraer los nutrientes que nos interesan
        resultado = {
            'fdc_id':    fdc_id,
            'fdc_nombre': food.get('description', ''),
            'ZINC':    None,
            'YODO':    None,
            'SELENIO': None,
        }

        for nutriente in detalle.get('foodNutrients', []):
            n_id = nutriente.get('nutrient', {}).get('id')
            valor = nutriente.get('amount')
            if n_id == NUTRIENT_IDS['ZINC']:
                resultado['ZINC'] = valor
            elif n_id == NUTRIENT_IDS['YODO']:
                resultado['YODO'] = valor
            elif n_id == NUTRIENT_IDS['SELENIO']:
                resultado['SELENIO'] = valor

        cache[query] = resultado
        return resultado

    except Exception as e:
        print(f"    ⚠ Error buscando '{query}': {e}")
        cache[query] = None
        return None


def enriquecer():
    """Proceso principal de enriquecimiento."""
    print("═" * 60)
    print("  NutriPlan — Enriquecimiento USDA FoodData Central")
    print("═" * 60)

    if API_KEY == 'DEMO_KEY':
        print("⚠ Usando DEMO_KEY — límite de 30 requests/hora")
        print("  Configurá FDC_API_KEY en .env para límite completo")
    else:
        print(f"✓ API Key configurada ({API_KEY[:8]}...)")

    # Cargar tabla de alimentos
    print("\nCargando tabla nutricional...")
    df = cargar_tabla('data/tabla_composicion_alimentos.xlsx')
    df_disp = df[df['DISPONIBLE']].copy()
    print(f"  {len(df_disp)} alimentos disponibles para enriquecer")

    # Cargar caché existente
    cache = cargar_cache()
    print(f"  {len(cache)} búsquedas en caché")

    resultados = []
    total = len(df_disp)
    sin_datos = 0
    con_datos = 0

    print(f"\nConsultando USDA API...")
    print("─" * 60)

    for i, (_, row) in enumerate(df_disp.iterrows()):
        alimento = row['ALIMENTO']
        query_en = traducir(alimento)

        # Mostrar progreso cada 10 alimentos
        if i % 10 == 0:
            print(f"  [{i+1}/{total}] {alimento} → '{query_en}'")

        datos = buscar_alimento_usda(query_en, cache)

        if datos:
            resultados.append({
                'ALIMENTO':   alimento,
                'ESTADO':     row['ESTADO'],
                'GRUPO':      row['GRUPO'],
                'ZINC_mg':    datos.get('ZINC'),
                'YODO_ug':    datos.get('YODO'),
                'SELENIO_ug': datos.get('SELENIO'),
                'FDC_ID':     datos.get('fdc_id'),
                'FDC_NOMBRE': datos.get('fdc_nombre'),
            })
            con_datos += 1
        else:
            resultados.append({
                'ALIMENTO':   alimento,
                'ESTADO':     row['ESTADO'],
                'GRUPO':      row['GRUPO'],
                'ZINC_mg':    None,
                'YODO_ug':    None,
                'SELENIO_ug': None,
                'FDC_ID':     None,
                'FDC_NOMBRE': None,
            })
            sin_datos += 1

        # Guardar caché cada 20 búsquedas
        if i % 20 == 0:
            guardar_cache(cache)

        # Rate limiting: 1000 req/hora = ~1 req/3.6s
        # Siendo conservadores: 1 req/2s para no agotar el límite
        time.sleep(2)

    # Guardar caché final
    guardar_cache(cache)

    # Guardar resultado
    df_resultado = pd.DataFrame(resultados)
    output_path = Path('data/enriquecimiento_usda.csv')
    df_resultado.to_csv(output_path, index=False, encoding='utf-8')

    print("\n" + "═" * 60)
    print(f"  ✓ Enriquecimiento completado")
    print(f"  Alimentos con datos USDA: {con_datos}/{total}")
    print(f"  Alimentos sin datos:      {sin_datos}/{total}")
    print(f"  Archivo guardado: {output_path}")
    print("═" * 60)

    # Mostrar muestra de resultados
    print("\nMuestra de resultados:")
    muestra = df_resultado.dropna(subset=['ZINC_mg']).head(10)
    print(muestra[['ALIMENTO', 'GRUPO', 'ZINC_mg', 'YODO_ug', 'SELENIO_ug',
                   'FDC_NOMBRE']].to_string(index=False))


if __name__ == '__main__':
    enriquecer()
