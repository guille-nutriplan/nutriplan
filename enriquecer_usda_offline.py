"""
enriquecer_usda_offline.py
Enriquecimiento offline usando la base SR Legacy de USDA.

Requiere haber descargado y descomprimido:
  https://fdc.nal.usda.gov/fdc-datasets/FoodData_Central_sr_legacy_food_csv_2018-04.zip

Copiar a data/usda_sr/:
  - food.csv
  - food_nutrient.csv

Genera: data/enriquecimiento_usda.csv
  Con columnas: ALIMENTO, ESTADO, GRUPO, ZINC_mg, YODO_ug, SELENIO_ug,
                FDC_ID, FDC_NOMBRE, SCORE_MATCH

Uso:
    python enriquecer_usda_offline.py
"""

import sys
import pandas as pd
import numpy as np
from pathlib import Path
from rapidfuzz import process, fuzz

sys.path.insert(0, str(Path(__file__).parent))
from data.tabla_loader import cargar_tabla

# ─── Rutas ────────────────────────────────────────────────────────────────────
DIR_USDA   = Path('data/usda_sr')
FOOD_CSV   = DIR_USDA / 'food.csv'
NUT_CSV    = DIR_USDA / 'food_nutrient.csv'
OUTPUT_CSV = Path('data/enriquecimiento_usda.csv')

# ─── IDs de nutrientes USDA ───────────────────────────────────────────────────
NUT_ZINC    = 1095   # Zinc (mg/100g)
NUT_YODO    = 1100   # Iodine (µg/100g)
NUT_SELENIO = 1103   # Selenium (µg/100g)

# Score mínimo de similitud para aceptar un match (0-100)
SCORE_MIN = 65

# ─── Diccionario de traducción español → inglés ───────────────────────────────
# Cuanto más específico, mejor el match
TRADUCCIONES = {
    # Cereales
    'arroz':              'rice white long-grain raw',
    'arroz integral':     'rice brown raw',
    'harina trigo':       'wheat flour white',
    'harina maiz':        'cornmeal',
    'maicena':            'cornstarch',
    'fideos':             'pasta dry',
    'macarrones':         'macaroni dry',
    'tallarines':         'noodles dry',
    'pan blanco':         'bread white commercially prepared',
    'pan integral':    'bread whole-wheat commercially prepared',
    'pan de maiz':        'bread cornbread',
    'galletas':           'crackers',
    'avena':              'oats rolled',
    'copos de maiz':      'corn flakes cereal',
    'polenta':            'cornmeal yellow',
    # Leguminosas
    'lenteja':            'lentils raw',
    'poroto':             'beans kidney raw',
    'garbanzo':           'chickpeas raw',
    'arvejas':            'peas green raw',
    'soja harina':        'soybean flour',
    'soja':               'soybeans raw',
    'haba':               'fava beans raw',
    # Hortalizas
    'papa':               'potato raw',
    'patata':             'potato raw',
    'batata':             'sweet potato raw',
    'zapallo':            'pumpkin raw',
    'zanahoria':          'carrot raw',
    'tomate':             'tomato raw',
    'lechuga':            'lettuce raw',
    'espinaca':           'spinach raw',
    'acelga':             'chard swiss raw',
    'cebolla':            'onion raw',
    'ajo':                'garlic raw',
    'brocoli':            'broccoli raw',
    'coliflor':           'cauliflower raw',
    'choclo':             'corn sweet raw',
    'pepino':             'cucumber raw',
    'berenjena':          'eggplant raw',
    'remolacha':          'beets raw',
    'apio':               'celery raw',
    'pimiento':           'pepper sweet raw',
    'morron':             'pepper sweet raw',
    'berros':             'watercress raw',
    'repollo':            'cabbage raw',
    'perejil':            'parsley raw',
    'mostaza':            'mustard greens raw',
    # Frutas
    'manzana':            'apple raw',
    'naranja':            'orange raw',
    'banana':             'banana raw',
    'pera':               'pear raw',
    'uva':                'grapes raw',
    'durazno':            'peach raw',
    'damasco':            'apricot raw',
    'frutilla':           'strawberries raw',
    'limon':              'lemon raw',
    'pomelo':             'grapefruit raw',
    'kiwi':               'kiwifruit raw',
    'sandia':             'watermelon raw',
    'melon':              'cantaloupe raw',
    'ciruela':            'plum raw',
    'mandarina':          'tangerine raw',
    'piña':               'pineapple raw',
    'pina':               'pineapple raw',
    'cereza':             'cherries raw',
    'higo':               'figs raw',
    'albaricoque':        'apricot raw',
    'frambuesa':          'raspberries raw',
    # Frutos secos
    'almendra':           'almonds',
    'nuez':               'walnuts',
    'mani':               'peanuts raw',
    'avellana':           'hazelnuts',
    'pistacho':           'pistachios',
    'castaña':            'chestnuts raw',
    'nuez brasil':        'brazil nuts',
    'nuez de coco':       'coconut raw',
    # Lácteos
    'leche entera':       'milk whole fluid',
    'leche descremada':   'milk nonfat fluid',
    'leche':              'milk whole fluid',
    'yogur':              'yogurt plain whole milk',
    'queso':              'cheese cheddar',
    'queso gruyere':      'cheese gruyere',
    'queso cottage':      'cheese cottage',
    'ricota':             'cheese ricotta',
    'manteca':            'butter',
    'crema':              'cream heavy whipping',
    'leche condensada':   'milk condensed sweetened',
    # Huevos
    'huevo entero':       'egg whole raw',
    'clara de huevo':     'egg white raw',
    'yema de huevo':      'egg yolk raw',
    'huevo':              'egg whole raw',
    # Aceites y grasas
    'aceite de girasol':  'oil sunflower',
    'aceite de maiz':     'oil corn',
    'aceite de oliva':    'oil olive',
    'aceite de soja':     'oil soybean',
    'aceite':             'oil vegetable',
    'manteca cerdo':      'lard',
    'margarina':          'margarine',
    # Azúcares
    'azucar':             'sugar white granulated',
    'miel':               'honey',
    'dulce de leche':     'caramel sauce',
    'mermelada':          'jam strawberry',
    'chocolate lacteado': 'chocolate milk',
    'chocolate amargo':   'chocolate dark',
    # Carnes
    'bife':               'beef sirloin raw',
    'carne picada':       'beef ground raw',
    'asado':              'beef ribs raw',
    'nalga':              'beef round raw',
    'lomo':               'beef tenderloin raw',
    'higado vaca':        'beef liver raw',
    'higado':             'beef liver raw',
    'carne vaca':         'beef raw',
    'cerdo':              'pork loin raw',
    'bondiola':           'pork shoulder raw',
    'cordero':            'lamb raw',
    'ternera':            'veal raw',
    # Aves
    'pollo':              'chicken whole raw',
    'pechuga':            'chicken breast raw',
    'muslo pollo':        'chicken thigh raw',
    'pato':               'duck raw',
    'pavo':               'turkey whole raw',
    # Pescados
    'merluza':            'fish hake raw',
    'atun':               'tuna canned water',
    'sardina':            'sardines canned oil',
    'salmon':             'salmon atlantic raw',
    'caballa':            'mackerel raw',
    'calamar':            'squid raw',
    'langostino':         'shrimp raw',
    'abadejo':            'fish pollock raw',
    # Embutidos
    'jamon':              'ham cured',
    'salchicha':          'frankfurter beef',
    'mortadela':          'bologna',
    'chorizo':            'sausage pork',
    'salame':             'salami',
    # Plurales y formas alternativas
    'garbanzos':           'chickpeas raw',
    'habas':               'fava beans raw',
    'judias':              'beans snap raw',
    'lentejas':            'lentils raw',
    'acelgas':             'chard swiss raw',
    'nabos':               'turnips raw',
    'puerros':             'leeks raw',
    'rabanos':             'radishes raw',
    'esparragos':          'asparagus raw',
    'champiñones':         'mushrooms raw',
    'fresas':              'strawberries raw',
    'ciruelas':            'plums raw',
    'cerezas':             'cherries sweet raw',
    'higos':               'figs raw',
    'uvas':                'grapes raw',
    'naranjas':            'oranges raw',
    'manzanas':            'apples raw',
    'bananas':             'bananas raw',
    'peras':               'pears raw',
    'melocotones':         'peaches raw',
    'albaricoques':        'apricots raw',
    'lentejas secas':      'lentils raw',
    'guisantes secos':     'peas split raw',
    # Items específicos sin match
    'salsifi':             'salsify raw',
    'seta':                'mushrooms shiitake raw',
    'pimienta':            'pepper spice',
    'buñuelos':            'doughnuts plain cake',
    'pan de maiz':         'cornbread dry mix',
    'acelga':              'chard swiss raw',
    'achicoria':           'chicory roots raw',
    # Cereales sin match
    'macarroneso fideos':  'pasta dry enriched',
    'pan de avena':        'bread oatmeal',
    # Frutas con nombres españoles (no rioplatenses)
    'melocoton':           'peaches raw',
    'fresa':               'strawberries raw',
    'freson':              'strawberries raw',
    'guindas':             'cherries sour raw',
    'membrillo':           'quince raw',
    'nispero':             'loquat raw',
    'ruibardo':            'rhubarb raw',
    'piña':                'pineapple raw',
    'caqui':               'persimmons raw',
    # Hortalizas sin match
    'acederas':            'sorrel raw',
    'trufa':               'mushrooms shiitake raw',
    'calabacin':           'zucchini summer squash raw',
    'judias rojas':        'beans kidney red raw',
    'pan de maiz':         'cornbread home-prepared',
    'pan de centeno':      'bread rye',
    'pan de maiz':         'bread cornbread',
    'pan de viena':        'bread french',
    'pan de trigo':        'bread wheat commercially prepared',
    'buñuelos':            'doughnuts cake type',
    'sagu':                'sago',
    'semola':              'semolina enriched',
    # Leguminosas sin match
    'judias blancas':      'beans white raw',
    'judias rojas':        'beans kidney red raw',
    # Hortalizas sin match
    'acederas':            'sorrel raw',
    'calabacin':           'zucchini raw',
    'cardo':               'cardoon raw',
    'chirivia':            'parsnip raw',
    'esparragos':          'asparagus raw',
    'espinacas':           'spinach raw',
    'aceitunas':           'olives ripe canned',
    'algas':               'seaweed raw',
    # Frutas sin match
    'chirimoyas':          'cherimoya raw',
    'guayaba':             'guava raw',
    'litchi':              'lychee raw',
    'maracuya':            'passion-fruit raw',
    'platano':             'plantains raw',
    'tamarindo':           'tamarind raw',
    # Correcciones de matches incorrectos
    'arroz':              'rice white long-grain raw',
    'perejil':            'parsley raw',
    'helado':             'ice cream',
    'tapioca':            'tapioca dry',
    # Vocabulario español sin traducción anterior
    'cebada':             'barley raw',
    'guisantes':          'peas green raw',
    'judias blancas':     'beans white raw',
    'judias rojas':       'beans kidney raw',
    'judias':             'beans snap raw',
    'acederas':           'sorrel raw',
    'alcachofa':          'artichokes raw',
    'calabaza':           'pumpkin raw',
    'calabacin':          'zucchini raw',
    'cardillo':           'thistle raw',
    'col de bruselas':    'brussels sprouts raw',
    'col rizada':         'kale raw',
    'col':                'cabbage raw',
    'colinabo':           'kohlrabi raw',
    'champiñon':          'mushrooms raw',
    'nabo':               'turnips raw',
    'puerro':             'leeks raw',
    'rabano':             'radishes raw',
    'endibias':           'endive raw',
    'escarola':           'endive raw',
    'hinojo':             'fennel raw',
    'alcaparras':         'capers canned',
    'aceitunas':          'olives ripe canned',
    # Frutas sin traducción
    'caqui':              'persimmons raw',
    'papaya':             'papaya raw',
    'mango':              'mango raw',
    'granada':            'pomegranate raw',
    'aguacate':           'avocado raw',
    'grosella':           'currants raw',
    'mora':               'blackberries raw',
    'arandano':           'blueberries raw',
    # Carnes específicas
    'higado de vaca':     'beef liver raw',
    'higado de cerdo':    'pork liver raw',
    'higado de pollo':    'chicken liver raw',
    'rinon':              'beef kidney raw',
    'corazon':            'beef heart raw',
    'mondongo':           'beef tripe raw',
    'costilla':           'beef ribs raw',
    'paleta':             'beef shoulder raw',
    'peceto':             'beef round raw',
    'vacío':              'beef flank raw',
    'matambre':           'beef flank steak raw',
    # Pescados específicos
    'trucha':             'trout raw',
    'dorado':             'fish mahi mahi raw',
    'pejerrey':           'fish smelt raw',
    'surubi':             'fish catfish raw',
    'corvina':            'fish drum raw',
    # Lácteos específicos
    'queso fresco':       'cheese fresh',
    'queso cremoso':      'cheese cream',
    'queso de bola':      'cheese edam',
    'queso port salut':   'cheese port salut',
    'queso reggianito':   'cheese parmesan',
    'leche en polvo':     'milk dry nonfat',
    # Embutidos
    'paleta cocida':      'ham cured',
    'bondiola cocida':    'pork shoulder cooked',
    'mortadela':          'bologna meat',
    # Otros
    'galletitas':         'crackers',
    'bizcocho':           'biscuits plain',
    'facturas':           'pastry danish',
    'medialunas':         'croissant',
    'torta':              'cake plain',
    'gelatina':           'gelatin dry',
    'levadura':           'yeast dry',
    'cacao':              'cocoa dry powder',
    'cafe':               'coffee brewed',
    'te':                 'tea brewed',
    'vinagre':            'vinegar',
    'sal':                'salt table',
}



# ─── Overrides manuales para matches problemáticos ────────────────────────────
# Alimentos donde el fuzzy matching falla consistentemente.
# FDC IDs de USDA SR Legacy verificados manualmente.
OVERRIDES_FDC = {
    'perejil':          11297,  # Parsley, fresh
    'arroz':            20051,  # Rice, white, long-grain, regular, cooked
    'guisantes secos':  11304,  # Peas, green, raw
    'piña':             9266,   # Pineapple, raw
    'pina':             9266,
    'calabacin':        11477,  # Squash, summer, zucchini, raw
}

def cargar_usda() -> tuple[pd.DataFrame, dict]:
    """
    Carga los CSVs de USDA SR Legacy y construye un lookup de nutrientes.

    Returns
    -------
    df_foods : DataFrame con fdc_id y descripción de cada alimento
    nutrientes : dict {fdc_id: {zinc, yodo, selenio}}
    """
    if not FOOD_CSV.exists() or not NUT_CSV.exists():
        raise FileNotFoundError(
            f"No se encontraron los archivos USDA en {DIR_USDA}/\n"
            f"Descargá el ZIP de SR Legacy desde:\n"
            f"https://fdc.nal.usda.gov/fdc-datasets/FoodData_Central_sr_legacy_food_csv_2018-04.zip\n"
            f"y copiá food.csv y food_nutrient.csv a {DIR_USDA}/"
        )

    print("Cargando base USDA SR Legacy...")
    df_foods = pd.read_csv(FOOD_CSV, usecols=['fdc_id', 'description'],
                           dtype={'fdc_id': int, 'description': str})
    print(f"  {len(df_foods):,} alimentos en SR Legacy")

    # Solo necesitamos los nutrientes que nos interesan
    print("Cargando nutrientes...")
    df_nut = pd.read_csv(
        NUT_CSV,
        usecols=['fdc_id', 'nutrient_id', 'amount'],
        dtype={'fdc_id': int, 'nutrient_id': int, 'amount': float}
    )
    df_nut = df_nut[df_nut['nutrient_id'].isin([NUT_ZINC, NUT_YODO, NUT_SELENIO])]

    # Construir diccionario {fdc_id: {zinc, yodo, selenio}}
    nutrientes = {}
    for _, row in df_nut.iterrows():
        fid = int(row['fdc_id'])
        if fid not in nutrientes:
            nutrientes[fid] = {'zinc': None, 'yodo': None, 'selenio': None}
        nid = int(row['nutrient_id'])
        val = row['amount']
        if nid == NUT_ZINC:
            nutrientes[fid]['zinc'] = val
        elif nid == NUT_YODO:
            nutrientes[fid]['yodo'] = val
        elif nid == NUT_SELENIO:
            nutrientes[fid]['selenio'] = val

    print(f"  {len(nutrientes):,} alimentos con datos de Zn/I/Se")
    return df_foods, nutrientes


def traducir_alimento(alimento: str, estado: str) -> str:
    """
    Convierte nombre en español a query en inglés para fuzzy matching.
    Usa matching de palabras completas (no substring) para evitar falsos positivos
    como 'pera' dentro de 'perejil'.
    """
    import re as _re
    al  = alimento.lower().strip()
    est = estado.lower().strip() if estado else ''

    # Ordenar por longitud de clave descendente (más específico primero)
    claves_ordenadas = sorted(TRADUCCIONES.keys(), key=len, reverse=True)

    for es in claves_ordenadas:
        en = TRADUCCIONES[es]
        # Matching de palabra completa — evita 'pera' dentro de 'perejil'
        patron = r'\b' + _re.escape(es) + r'\b'
        if _re.search(patron, al):
            if 'crudo' in est or 'crudos' in est or 'cruda' in est:
                if 'raw' not in en:
                    return en + ' raw'
            elif 'cocido' in est or 'cocida' in est:
                if 'cooked' not in en and 'raw' in en:
                    return en.replace(' raw', ' cooked')
            return en

    # Intentar con forma singular (quitar 's' final)
    al_singular = al.rstrip('s') if al.endswith('s') else al
    if al_singular != al:
        for es in claves_ordenadas:
            en = TRADUCCIONES[es]
            patron_s = r'\b' + _re.escape(es) + r'\b'
            if _re.search(patron_s, al_singular):
                return en

    return al


def enriquecer_offline():
    """Proceso principal de enriquecimiento offline."""
    print("═" * 65)
    print("  NutriPlan — Enriquecimiento Offline USDA SR Legacy")
    print("═" * 65)

    # Cargar USDA
    try:
        df_foods, nutrientes = cargar_usda()
    except FileNotFoundError as e:
        print(f"\n⚠ {e}")
        return

    # Preparar lista de nombres USDA para fuzzy matching
    usda_names    = df_foods['description'].str.lower().tolist()
    usda_fdc_ids  = df_foods['fdc_id'].tolist()

    # Cargar nuestra tabla
    print("\nCargando tabla nutricional argentina...")
    df = cargar_tabla('data/tabla_composicion_alimentos.xlsx')
    df_disp = df[df['DISPONIBLE']].drop_duplicates(subset=['ALIMENTO', 'GRUPO']).copy()
    print(f"  {len(df_disp)} alimentos únicos para enriquecer")

    resultados = []
    matcheados = 0
    sin_match  = 0

    print(f"\nBuscando coincidencias (fuzzy matching)...")
    print("─" * 65)

    for i, (_, row) in enumerate(df_disp.iterrows()):
        alimento = row['ALIMENTO']
        estado   = row['ESTADO']
        grupo    = row['GRUPO']
        query    = traducir_alimento(alimento, estado)

        # Aplicar overrides manuales primero
        fdc_override = OVERRIDES_FDC.get(alimento.lower().strip())
        if fdc_override:
            override_name = df_foods[df_foods['fdc_id'] == fdc_override]
            if len(override_name):
                fdc_id = fdc_override
                nuts   = nutrientes.get(fdc_id, {})
                nombre = override_name['description'].iloc[0]
                resultados.append({
                    'ALIMENTO': alimento, 'ESTADO': estado, 'GRUPO': grupo,
                    'ZINC_mg': nuts.get('zinc'), 'YODO_ug': nuts.get('yodo'),
                    'SELENIO_ug': nuts.get('selenio'),
                    'FDC_ID': fdc_id, 'FDC_NOMBRE': nombre, 'SCORE': 100.0,
                })
                matcheados += 1
                continue

        # Fuzzy match contra todos los nombres USDA
        matches = process.extract(
            query,
            usda_names,
            scorer=fuzz.token_sort_ratio,
            limit=1
        )

        if matches and matches[0][1] >= SCORE_MIN:
            nombre_match, score, idx = matches[0]
            fdc_id = usda_fdc_ids[idx]
            nuts   = nutrientes.get(fdc_id, {})

            resultados.append({
                'ALIMENTO':   alimento,
                'ESTADO':     estado,
                'GRUPO':      grupo,
                'ZINC_mg':    nuts.get('zinc'),
                'YODO_ug':    nuts.get('yodo'),
                'SELENIO_ug': nuts.get('selenio'),
                'FDC_ID':     fdc_id,
                'FDC_NOMBRE': df_foods[df_foods['fdc_id'] == fdc_id]['description'].iloc[0],
                'SCORE':      round(score, 1),
            })
            matcheados += 1

            if i % 20 == 0:
                print(f"  [{i+1}/{len(df_disp)}] '{alimento}' → '{nombre_match}' (score: {score:.0f})")
        else:
            resultados.append({
                'ALIMENTO':   alimento,
                'ESTADO':     estado,
                'GRUPO':      grupo,
                'ZINC_mg':    None,
                'YODO_ug':    None,
                'SELENIO_ug': None,
                'FDC_ID':     None,
                'FDC_NOMBRE': None,
                'SCORE':      0,
            })
            sin_match += 1

    df_res = pd.DataFrame(resultados)
    df_res.to_csv(OUTPUT_CSV, index=False, encoding='utf-8')

    print("\n" + "═" * 65)
    print(f"  ✓ Completado")
    print(f"  Con match:   {matcheados}/{len(df_disp)}  ({matcheados/len(df_disp)*100:.0f}%)")
    print(f"  Sin match:   {sin_match}/{len(df_disp)}")
    print(f"  Guardado en: {OUTPUT_CSV}")

    # Muestra de resultados con mejor score
    print("\nMuestra — mejores matches:")
    top = df_res[df_res['SCORE'] >= 70].sort_values('SCORE', ascending=False).head(15)
    print(top[['ALIMENTO', 'GRUPO', 'ZINC_mg', 'SELENIO_ug',
               'FDC_NOMBRE', 'SCORE']].to_string(index=False))

    print("\nAlimentos sin match (para expandir diccionario):")
    sin = df_res[df_res['SCORE'] < SCORE_MIN][['ALIMENTO', 'GRUPO']].head(20)
    print(sin.to_string(index=False))


if __name__ == '__main__':
    enriquecer_offline()
