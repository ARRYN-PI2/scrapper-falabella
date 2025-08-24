# scrape_falabella_all.py
import time
import json
import re
import random
import logging
import os
import os.path as osp
from dataclasses import dataclass, asdict
from typing import List, Tuple, Optional, Set, Dict
from datetime import datetime
from urllib.parse import urlparse

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from selenium.common.exceptions import (
    StaleElementReferenceException,
    ElementClickInterceptedException,
    TimeoutException,
    NoSuchElementException,
)

from webdriver_manager.chrome import ChromeDriverManager


# =========================
# CONFIG & LOGGING
# =========================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

OUT_DIR = os.getenv("OUTPUT_DIR", "/app/out")
os.makedirs(OUT_DIR, exist_ok=True)

LOGGER = logging.getLogger("falabella_all_scraper")

HOME_URL = "https://www.falabella.com.co/falabella-co/"
OUTPUT_JSON = osp.join(OUT_DIR, "productos_all.json")
OUTPUT_JSONL = osp.join(OUT_DIR, "productos_all.jsonl")

# Limitar cantidad de categorías (None = todas las que encuentre)
MAX_CATEGORIES: Optional[int] = None  # p.ej. 5 para pruebas

# Limitar a 1 página por categoría
LIMIT_ONE_PAGE_PER_CATEGORY: bool = True  # <- pon True para modo rápido

# Patrones para filtrar “pods” promocionales que no son productos reales
PROMO_TITLE_PAT = re.compile(
    r'^\s*(env[ií]o\s+gratis|por\s+falabella|vendid[oa]\s+por\s+falabella|exclusivo\s+falabella|marketplace\s+falabella)\b',
    re.I
)

# NUEVO: filtrar títulos tipo "Por Calm.", "Por X", etc.
TITLE_EXCLUDE_PAT = re.compile(r'^\s*por\b', re.I)

# Throttle base
def nap(a=0.4, b=1.0):
    time.sleep(random.uniform(a, b))


# =========================
# UTILIDADES / PARSEOS
# =========================
def limpiar_precio(precio_raw: str) -> Tuple[str, Optional[int], Optional[str]]:
    """
    Normaliza el precio y devuelve:
    (texto_normalizado, valor_numérico_entero, moneda)
    """
    if not precio_raw or precio_raw == "N/A":
        return ("N/A", None, None)
    m = re.search(r"(\$)\s*([\d\.\,]+)", precio_raw)
    if not m:
        return ("N/A", None, None)
    simbolo, cifra = m.group(1), m.group(2)
    valor = int(re.sub(r"[^\d]", "", cifra)) if re.search(r"\d", cifra) else None
    return (f"{simbolo} {cifra}", valor, "COP")


def extraer_tamano_desde_titulo(titulo: str) -> str:
    """
    Intento genérico: si aparece un tamaño en pulgadas en el título.
    Si no aplica (categorías que no son TV, monitores, etc.), retorna "N/A".
    """
    m = re.search(r'(\d{2,3})\s*(?:["”]|pulgadas?|in\b)', titulo, re.I)
    return (m.group(1) + '"') if m else "N/A"


def parsear_marca_desde_titulo(titulo: str) -> str:
    """
    Heurística simple para marca: primera palabra “significativa”.
    Si no es evidente, retorna "N/A".
    """
    partes = re.split(r"\s+|-|–|—", titulo)
    for p in partes:
        w = re.sub(r"[^A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9]", "", p)
        if w and w.lower() not in {"tv", "smart", "led", "uhd", "4k", "full", "hd", "de", "para", "por"} and len(w) >= 2:
            return w.upper()
    return "N/A"


# =========================
# EXTRACCIONES / SELECTORES
# =========================
def scroll_cargar_todos(
    driver,
    contenedor_selector="#testId-searchResults-products",
    max_sin_cambios=6,
    paso_px=1600,
    espera=1.8
) -> int:
    """
    Scrollea hasta que no haya cambios de altura / nuevos pods por 'max_sin_cambios' iteraciones.
    """
    seen = 0
    sin_cambios = 0
    last_height = driver.execute_script("return document.body.scrollHeight")
    while sin_cambios < max_sin_cambios:
        driver.execute_script(f"window.scrollBy(0, {paso_px});")
        time.sleep(espera)
        try:
            contenedor = driver.find_element(By.CSS_SELECTOR, contenedor_selector)
            pods = contenedor.find_elements(By.CSS_SELECTOR, "a[data-pod='catalyst-pod']")
            nuevos = len(pods)
        except StaleElementReferenceException:
            nuevos = seen

        height = driver.execute_script("return document.body.scrollHeight")
        if nuevos == seen and height == last_height:
            sin_cambios += 1
        else:
            sin_cambios = 0
            seen = nuevos
            last_height = height
    return seen


def extraer_precio_listado(pod) -> Tuple[str, Optional[int], Optional[str]]:
    selectores = [
        "[data-testid='current-price']",
        "span[data-testid*='current']",
        "[class*='price']",
        "li[class*='price']",
        "span"
    ]
    for sel in selectores:
        for el in pod.find_elements(By.CSS_SELECTOR, sel):
            txt = el.text.strip()
            if "$" in txt:
                return limpiar_precio(txt)
    return ("N/A", None, None)


def extraer_calificacion_listado(pod) -> str:
    try:
        el = pod.find_element(By.CSS_SELECTOR, "[data-rating]")
        val = el.get_attribute("data-rating")
        return val.strip() if val else "N/A"
    except Exception:
        return "N/A"


def extraer_calificacion_ficha(driver) -> str:
    """
    Fallback en la ficha usando aria-label estilo "4.5 de 5".
    """
    try:
        el = driver.find_element(By.XPATH, "//*[@aria-label[contains(., 'de 5')]]")
        t = el.get_attribute("aria-label") or el.text
        m = re.search(r"(\d+(?:[.,]\d+)?)\s*de\s*5", t or "", re.I)
        return m.group(1).replace(",", ".") if m else "N/A"
    except Exception:
        return "N/A"


def extraer_detalles_ficha_texto(driver) -> str:
    """
    Devuelve el TEXTO PLANO del div#productInfoContainer (sin etiquetas).
    """
    try:
        el = WebDriverWait(driver, 12).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "#productInfoContainer"))
        )
        return el.text.strip()
    except Exception:
        return ""


def obtener_nombre_categoria(driver) -> str:
    """
    Intenta obtener un título/nombre de la categoría desde la página:
    - h1 principal
    - breadcrumb
    - fallback: derivar desde URL
    """
    try:
        h1 = driver.find_element(By.TAG_NAME, "h1").text.strip()
        if h1:
            return h1
    except Exception:
        pass

    try:
        crumb = driver.find_elements(By.CSS_SELECTOR, "nav [aria-label*='breadcrumb' i], nav[aria-label*='breadcrumb' i]")
        if crumb:
            txt = crumb[0].text.strip()
            if txt:
                return txt.split("\n")[-1].strip()
    except Exception:
        pass

    try:
        path = urlparse(driver.current_url).path
        parts = [p for p in path.split("/") if p]
        if "category" in parts:
            idx = parts.index("category")
            if idx + 1 < len(parts):
                raw = parts[idx + 1]
                return re.sub(r"[-_]+", " ", raw).strip().title()
    except Exception:
        pass
    return "N/A"


# =========================
# CONTADOR GLOBAL
# =========================
_EXTRACCION_TOTAL = 1  # NUEVO: contador de extracción total


# =========================
# MODELO DE DATO
# =========================
@dataclass
class Producto:
    # NUEVO: primero el contador de extracción total
    contador_extraccion_total: int
    contador_extraccion: int
    titulo: str
    marca: str
    precio_texto: str
    precio_valor: Optional[int]
    moneda: Optional[str]
    tamaño: str
    calificacion: str
    detalles_adicionales: str  # TEXTO PLANO del #productInfoContainer
    fuente: str
    categoria: str
    imagen: str
    link: str
    pagina: int
    fecha_extraccion: str
    extraction_status: str


# =========================
# EXTRACCIÓN DE UNA PÁGINA (GENÉRICA)
# =========================
def extraer_productos_pagina(
    driver,
    contador_inicio=1,
    pagina_actual=1,
    categoria_actual="N/A",
    obtener_detalles=True
) -> Tuple[List[Producto], int]:
    global _EXTRACCION_TOTAL

    scroll_cargar_todos(driver)
    pods = driver.find_elements(By.CSS_SELECTOR, "#testId-searchResults-products a[data-pod='catalyst-pod']")
    LOGGER.info(f"[{categoria_actual}] Pods detectados en página {pagina_actual}: {len(pods)}")

    productos: List[Producto] = []
    contador = contador_inicio

    for i, pod in enumerate(pods, start=1):
        try:
            link = pod.get_attribute("href")
            if not link:
                continue

            # imagen y título
            img_elem = pod.find_elements(By.CSS_SELECTOR, "img[id^='testId-pod-image']")
            if img_elem:
                imagen = img_elem[0].get_attribute("src") or "N/A"
                titulo = (img_elem[0].get_attribute("alt") or "").strip()
            else:
                titulo_elem = pod.find_elements(By.CSS_SELECTOR, "span, h2, h3, p")
                titulo = (titulo_elem[0].text.strip() if titulo_elem else "")
                imagen = "N/A"

            if not titulo:
                titulo = "N/A"

            # --- Filtro: títulos no deseados ---
            # 1) Promocionales
            if titulo != "N/A" and PROMO_TITLE_PAT.search(titulo):
                LOGGER.debug(f"[{categoria_actual}] Pod promocional filtrado: '{titulo}'")
                continue
            # 2) Títulos que empiezan con "Por ..."
            if titulo != "N/A" and TITLE_EXCLUDE_PAT.search(titulo):
                LOGGER.debug(f"[{categoria_actual}] Pod filtrado por título 'Por ...': '{titulo}'")
                continue

            marca = parsear_marca_desde_titulo(titulo) if titulo not in ("", "N/A") else "N/A"
            precio_txt, precio_num, moneda = extraer_precio_listado(pod)
            tamano = extraer_tamano_desde_titulo(titulo) if titulo not in ("", "N/A") else "N/A"
            calificacion = extraer_calificacion_listado(pod)
            detalles_adicionales = ""

            # Abrir ficha UNA sola vez para obtener detalles y rating fallback
            if obtener_detalles or calificacion in {"N/A", "", "0"}:
                original_window = driver.current_window_handle
                driver.execute_script("window.open(arguments[0]);", link)
                driver.switch_to.window(driver.window_handles[-1])
                try:
                    WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.TAGNAME, "body")))
                except Exception:
                    # Algunos drivers usan By.TAG_NAME (corrigamos al vuelo)
                    WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
                try:
                    nap(1.0, 2.0)
                    if obtener_detalles:
                        detalles_adicionales = extraer_detalles_ficha_texto(driver)
                    if calificacion in {"N/A", "", "0"}:
                        calificacion = extraer_calificacion_ficha(driver)
                except Exception:
                    pass
                finally:
                    driver.close()
                    driver.switch_to.window(original_window)

            producto = Producto(
                contador_extraccion_total=_EXTRACCION_TOTAL,  # NUEVO
                contador_extraccion=contador,
                titulo=titulo if titulo else "N/A",
                marca=marca,
                precio_texto=precio_txt,
                precio_valor=precio_num,
                moneda=moneda,
                tamaño=tamano,
                calificacion=calificacion if calificacion else "N/A",
                detalles_adicionales=detalles_adicionales,
                fuente="Falabella",
                categoria=categoria_actual,
                imagen=imagen,
                link=link,
                pagina=pagina_actual,
                fecha_extraccion=datetime.now().isoformat(),
                extraction_status="success" if precio_num is not None else "failed"
            )

            # Otra barrera anti-promos: si no hay precio y el título es no deseado, descartamos
            if producto.precio_valor is None and (PROMO_TITLE_PAT.search(producto.titulo or "") or TITLE_EXCLUDE_PAT.search(producto.titulo or "")):
                LOGGER.debug(f"[{categoria_actual}] Pod sin precio filtrado: '{producto.titulo}'")
                continue

            productos.append(producto)
            LOGGER.debug(f"[{categoria_actual}] P{pagina_actual} #{contador} / T#{_EXTRACCION_TOTAL}: {marca} | {titulo} | {precio_txt} | ★ {calificacion}")
            contador += 1
            _EXTRACCION_TOTAL += 1  # incrementa contador global

            nap(0.25, 0.7)

        except Exception as e:
            LOGGER.debug(f"[{categoria_actual}] Error en pod {i} de página {pagina_actual}: {e}")

    return productos, contador


# =========================
# NAVEGACIÓN PAGINACIÓN
# =========================
def ir_a_siguiente_pagina(driver) -> bool:
    """
    Intenta clickear el botón de siguiente usando varios selectores.
    Retorna True si cambia de página (click con staleness), False si no hay siguiente.
    """
    try:
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        nap(0.8, 1.8)

        posibles_botones = driver.find_elements(By.CSS_SELECTOR, "button[id*='pagination'][id*='arrow-right']")
        if not posibles_botones:
            posibles_botones = driver.find_elements(By.CSS_SELECTOR, "button.btn.pagination-arrow")
        if not posibles_botones:
            posibles_botones = driver.find_elements(By.CSS_SELECTOR, "li[class*='pagination-arrow'] a, a[class*='pagination-arrow']")

        if not posibles_botones:
            return False

        siguiente_btn = posibles_botones[-1]
        pods = driver.find_elements(By.CSS_SELECTOR, "#testId-searchResults-products a[data-pod='catalyst-pod']")
        primer_pod = pods[0] if pods else None

        try:
            driver.execute_script("arguments[0].click();", siguiente_btn)
        except ElementClickInterceptedException:
            nap(0.6, 1.2)
            driver.execute_script("arguments[0].click();", siguiente_btn)

        if primer_pod:
            WebDriverWait(driver, 15).until(EC.staleness_of(primer_pod))
        else:
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "#testId-searchResults-products"))
            )
        nap(0.8, 1.6)
        return True

    except TimeoutException:
        return False
    except Exception:
        return False


# =========================
# DRIVER / OPTIONS
# =========================
def crear_driver() -> webdriver.Chrome:
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--lang=es-CO")
    options.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                         "AppleWebKit/537.36 (KHTML, like Gecko) "
                         "Chrome/124.0.0.0 Safari/537.36")

    remote_url = os.getenv("SELENIUM_REMOTE_URL")
    if remote_url:
        # Usar navegador remoto (Selenium Standalone Chrome)
        return webdriver.Remote(command_executor=remote_url, options=options)
    else:
        # Fallback local (tu código actual con ChromeDriverManager)
        from selenium.webdriver.chrome.service import Service
        from webdriver_manager.chrome import ChromeDriverManager
        service = Service(ChromeDriverManager().install())
        return webdriver.Chrome(service=service, options=options)


# =========================
# PERSISTENCIA
# =========================
def guardar_json(productos: List[Producto], ruta=OUTPUT_JSON):
    with open(ruta, "w", encoding="utf-8") as f:
        json.dump([asdict(p) for p in productos], f, ensure_ascii=False, indent=4)


def append_jsonl(producto: Producto, ruta=OUTPUT_JSONL):
    with open(ruta, "a", encoding="utf-8") as f:
        f.write(json.dumps(asdict(producto), ensure_ascii=False) + "\n")


# =========================
# DESCUBRIMIENTO DE CATEGORÍAS
# =========================
def descubrir_links_categorias(driver) -> Dict[str, str]:
    """
    Abre la home y recolecta enlaces que parezcan categorías:
    - Rutas que contengan '/category/' (principal)
    - Como respaldo, algunos '/search?Ntt=' destacados
    Retorna dict {nombre_categoria: url}
    """
    driver.get(HOME_URL)
    WebDriverWait(driver, 15).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
    nap(1.5, 2.5)

    enlaces = driver.find_elements(By.CSS_SELECTOR, "a[href]")
    cats: Dict[str, str] = {}
    vistos: Set[str] = set()

    for a in enlaces:
        try:
            href = a.get_attribute("href") or ""
            if not href:
                continue
            if "falabella.com.co/falabella-co" not in href:
                continue

            if "/category/" in href:
                txt = (a.text or "").strip()
                nombre = txt if txt else derivar_nombre_desde_url(href)
                if nombre and href not in vistos:
                    cats[nombre] = href
                    vistos.add(href)
            elif "/search?" in href and ("Ntt=" in href or "categoryId=" in href):
                txt = (a.text or "").strip()
                nombre = txt if txt else derivar_nombre_desde_url(href)
                if nombre and href not in vistos:
                    cats[nombre] = href
                    vistos.add(href)
        except Exception:
            continue

    LOGGER.info(f"Categorias descubiertas: {len(cats)}")
    return cats


def derivar_nombre_desde_url(href: str) -> str:
    try:
        path = urlparse(href).path
        parts = [p for p in path.split("/") if p]
        if "category" in parts:
            i = parts.index("category")
            if i + 1 < len(parts):
                raw = parts[i + 1]
                return re.sub(r"[-_]+", " ", raw).strip().title()
        if parts:
            raw = parts[-1]
            return re.sub(r"[-_]+", " ", raw).strip().title()
    except Exception:
        pass
    return ""


# =========================
# EXTRACCIÓN POR CATEGORÍA
# =========================
def extraer_categoria(
    driver,
    url_categoria: str,
    nombre_categoria: Optional[str] = None,
    limit_one_page: bool = False
) -> List[Producto]:
    driver.get(url_categoria)
    WebDriverWait(driver, 15).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
    nap(1.2, 2.0)

    categoria_nombre = nombre_categoria or obtener_nombre_categoria(driver) or "N/A"
    LOGGER.info(f"==> Categoria: {categoria_nombre} | {url_categoria}")

    productos_totales: List[Producto] = []
    vistos: Set[str] = set()
    pagina = 1
    contador = 1

    while True:
        productos, contador = extraer_productos_pagina(
            driver,
            contador_inicio=contador,
            pagina_actual=pagina,
            categoria_actual=categoria_nombre,
            obtener_detalles=True  # requerido: traer detalles del productInfoContainer (texto)
        )

        # deduplicación por link
        nuevos: List[Producto] = []
        for p in productos:
            if p.link not in vistos:
                vistos.add(p.link)
                nuevos.append(p)
                append_jsonl(p)  # guardado incremental

        if not nuevos:
            LOGGER.info(f"[{categoria_nombre}] No hay nuevos productos en esta página. Fin.")
            break

        productos_totales.extend(nuevos)

        # Limitar a una sola página por categoría
        if limit_one_page:
            LOGGER.info(f"[{categoria_nombre}] Modo 1 página por categoría: detenido en página {pagina}.")
            break

        # Intentar siguiente página
        if not ir_a_siguiente_pagina(driver):
            LOGGER.info(f"[{categoria_nombre}] No hay más páginas.")
            break

        pagina += 1
        nap(1.0, 2.0)

    return productos_totales


# =========================
# EJECUCIÓN COMPLETA
# =========================
def extraer_todas_categorias():
    driver = crear_driver()
    try:
        cats = descubrir_links_categorias(driver)
        if not cats:
            LOGGER.warning("No se encontraron categorías en la home. Revisa selectores o la disponibilidad del sitio.")
            return []

        items = list(cats.items())
        if isinstance(MAX_CATEGORIES, int) and MAX_CATEGORIES > 0:
            items = items[:MAX_CATEGORIES]

        total: List[Producto] = []
        for nombre, url in items:
            try:
                productos_cat = extraer_categoria(
                    driver,
                    url,
                    nombre_categoria=nombre,
                    limit_one_page=LIMIT_ONE_PAGE_PER_CATEGORY
                )
                total.extend(productos_cat)
                LOGGER.info(f"[{nombre}] Acumulado total: {len(total)} productos.")
            except Exception as e:
                LOGGER.warning(f"Error extrayendo categoría '{nombre}': {e}")
                continue

        guardar_json(total, OUTPUT_JSON)
        LOGGER.info(f"Guardados {len(total)} productos en {OUTPUT_JSON} y {OUTPUT_JSONL}.")
        return total

    finally:
        driver.quit()


# =========================
# MAIN
# =========================
if __name__ == "__main__":
    extraer_todas_categorias()
