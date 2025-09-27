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

OUT_DIR = osp.join(os.getcwd(), "out")
os.makedirs(OUT_DIR, exist_ok=True)

LOGGER = logging.getLogger("falabella_all_scraper")

HOME_URL = "https://www.falabella.com.co/falabella-co/"

# Archivos por defecto (fallback). Para scrapes por categoría se redefinen con set_run_outputs(...)
OUTPUT_JSON = osp.join(OUT_DIR, "productos_all.json")
OUTPUT_JSONL = osp.join(OUT_DIR, "productos_all.jsonl")

# Rutas activas de salida (se actualizan por corrida)
RUN_JSON = OUTPUT_JSON
RUN_JSONL = OUTPUT_JSONL

# Limitar cantidad de categorías al ejecutar TODAS (None = todas)
MAX_CATEGORIES: Optional[int] = None

# Por defecto: permitir múltiples páginas por categoría
LIMIT_ONE_PAGE_PER_CATEGORY: bool = False

# Modo rápido global (se puede activar por CLI con --fast)
FAST_MODE: bool = False

# Patrones para filtrar “pods” promocionales que no son productos reales
PROMO_TITLE_PAT = re.compile(
    r'^\s*(env[ií]o\s+gratis|por\s+falabella|vendid[oa]\s+por\s+falabella|exclusivo\s+falabella|marketplace\s+falabella)\b',
    re.I
)

# Filtrar títulos tipo "Por X"
TITLE_EXCLUDE_PAT = re.compile(r'^\s*por\b', re.I)


def nap(a=0.4, b=1.0):
    time.sleep(random.uniform(a, b))


def slugify(txt: str) -> str:
    """Convierte un nombre en un slug simple para archivos."""
    t = txt.strip().lower()
    t = re.sub(r"\s+", "_", t)
    t = re.sub(r"[^\w\-]+", "", t)  # deja letras/números/guion_bajo/guion
    return t or "salida"


def set_run_outputs(nombre_categoria: str) -> None:
    """
    Define archivos por corrida/categoría usando la CLAVE de EXPECTED_URLS (o el nombre pasado por CLI).
    Se crean/l limpian:
    - {slug}_formatted.json
    - {slug}_formatted.jsonl
    """
    global RUN_JSON, RUN_JSONL
    slug = slugify(nombre_categoria)
    RUN_JSON = osp.join(OUT_DIR, f"{slug}_formatted.json")
    RUN_JSONL = osp.join(OUT_DIR, f"{slug}_formatted.jsonl")

    os.makedirs(OUT_DIR, exist_ok=True)
    # Reinicia los archivos al iniciar un nuevo scrape de esta categoría
    with open(RUN_JSON, "w", encoding="utf-8") as f:
        f.write("[]")
    with open(RUN_JSONL, "w", encoding="utf-8") as _:
        pass

    LOGGER.info(f"🗂️ Salidas para '{nombre_categoria}':")
    LOGGER.info(f"   JSON  : {RUN_JSON}")
    LOGGER.info(f"   JSONL : {RUN_JSONL}")


# =========================
# UTILIDADES / PARSEOS
# =========================
def limpiar_precio(precio_raw: str) -> Tuple[str, Optional[int], Optional[str]]:
    if not precio_raw or precio_raw == "N/A":
        return ("N/A", None, None)
    m = re.search(r"(\$)\s*([\d\.\,]+)", precio_raw)
    if not m:
        return ("N/A", None, None)
    simbolo, cifra = m.group(1), m.group(2)
    valor = int(re.sub(r"[^\d]", "", cifra)) if re.search(r"\d", cifra) else None
    return (f"{simbolo} {cifra}", valor, "COP")


def extraer_tamano_desde_titulo(titulo: str) -> str:
    m = re.search(r'(\d{2,3})\s*(?:["”]|pulgadas?|in\b)', titulo, re.I)
    return (m.group(1) + '"') if m else "N/A"


def parsear_marca_desde_titulo(titulo: str) -> str:
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
    En FAST_MODE, ajusta para aún hacer un scroll suficiente para lazy-load.
    """
    if FAST_MODE:
        max_sin_cambios = 4
        espera = 1.0

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
    try:
        el = driver.find_element(By.XPATH, "//*[@aria-label[contains(., 'de 5')]]")
        t = el.get_attribute("aria-label") or el.text
        m = re.search(r"(\d+(?:[.,]\d+)?)\s*de\s*5", t or "", re.I)
        return m.group(1).replace(",", ".") if m else "N/A"
    except Exception:
        return "N/A"


def extraer_detalles_ficha_texto(driver) -> str:
    try:
        el = WebDriverWait(driver, 12).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "#productInfoContainer"))
        )
        return el.text.strip()
    except Exception:
        return ""


def obtener_nombre_categoria(driver) -> str:
    # 1) H1
    try:
        h1 = driver.find_element(By.TAG_NAME, "h1").text.strip()
        if h1:
            return h1
    except Exception:
        pass

    # 2) Breadcrumb
    try:
        crumb = driver.find_elements(By.CSS_SELECTOR, "nav [aria-label*='breadcrumb' i], nav[aria-label*='breadcrumb' i], nav.breadcrumb, ol.breadcrumb")
        if crumb:
            txt = crumb[0].text.strip()
            if txt:
                last = txt.split("\n")[-1].strip()
                if last:
                    return last
    except Exception:
        pass

    # 3) og:title
    try:
        og = driver.find_elements(By.CSS_SELECTOR, "meta[property='og:title'], meta[name='og:title']")
        if og:
            val = (og[0].get_attribute("content") or "").strip()
            if val:
                val = re.sub(r"\s*[\|\-–—]\s*Falabella.*$", "", val, flags=re.I).strip()
                if val:
                    return val
    except Exception:
        pass

    # 4) <title>
    try:
        t = (driver.title or "").strip()
        if t:
            t = re.sub(r"\s*[\|\-–—]\s*Falabella.*$", "", t, flags=re.I).strip()
            if t:
                return t
    except Exception:
        pass

    # 5) Fallback URL (evitar cat12345)
    try:
        path = urlparse(driver.current_url).path
        parts = [p for p in path.split("/") if p]
        if "category" in parts:
            idx = parts.index("category")
            if idx + 1 < len(parts):
                raw = parts[idx + 1]
                if not re.fullmatch(r"cat\d+", raw, flags=re.I):
                    nombre = re.sub(r"[-_]+", " ", raw).strip().title()
                    if nombre:
                        return nombre
        if parts:
            raw = parts[-1]
            if not re.fullmatch(r"cat\d+", raw, flags=re.I):
                nombre = re.sub(r"[-_]+", " ", raw).strip().title()
                if nombre:
                    return nombre
    except Exception:
        pass

    return "N/A"


# =========================
# CONTADOR GLOBAL
# =========================
_EXTRACCION_TOTAL = 1


# =========================
# MODELO DE DATO
# =========================
@dataclass
class Producto:
    contador_extraccion_total: int
    contador_extraccion: int
    titulo: str
    marca: str
    precio_texto: str
    precio_valor: Optional[int]
    moneda: Optional[str]
    tamaño: str
    calificacion: str
    detalles_adicionales: str
    fuente: str
    categoria: str
    imagen: str
    link: str
    pagina: int
    fecha_extraccion: str
    extraction_status: str


# =========================
# PERSISTENCIA (usa RUN_JSON / RUN_JSONL)
# =========================
def guardar_json(productos: List[Producto], ruta: Optional[str] = None):
    ruta = ruta or RUN_JSON
    with open(ruta, "w", encoding="utf-8") as f:
        json.dump([asdict(p) for p in productos], f, ensure_ascii=False, indent=4)


def append_jsonl(producto: Producto, ruta: Optional[str] = None):
    ruta = ruta or RUN_JSONL
    with open(ruta, "a", encoding="utf-8") as f:
        f.write(json.dumps(asdict(producto), ensure_ascii=False) + "\n")


# =========================
# CARGA ROBUSTA CON REINTENTOS
# =========================
def safe_get(driver, url: str, retries: int = 3, wait_between=(2.0, 4.0)) -> None:
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            driver.get(url)
            return
        except TimeoutException as e:
            last_err = e
            try:
                driver.execute_script("window.stop();")
                return
            except Exception:
                pass
            LOGGER.warning(f"safe_get timeout {attempt}/{retries} para {url}: {e}")
            nap(*wait_between)
        except Exception as e:
            last_err = e
            LOGGER.warning(f"safe_get error {attempt}/{retries} para {url}: {e}")
            nap(*wait_between)
    if last_err:
        raise last_err


# =========================
# DESCUBRIMIENTO DE CATEGORÍAS (no usado en CLI, se mantiene como helper)
# =========================
def descubrir_links_categorias(driver) -> Dict[str, str]:
    safe_get(driver, HOME_URL)
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


def resolver_categoria_por_nombre(nombre: str) -> Tuple[Optional[str], Optional[str]]:
    if not nombre:
        return None, None

    target = nombre.strip().lower()
    if not target:
        return None, None

    for k, url in EXPECTED_URLS.items():
        if k.strip().lower() == target:
            return k, url

    candidatos: List[Tuple[str, str]] = []
    for k, url in EXPECTED_URLS.items():
        kl = k.strip().lower()
        if target in kl or kl in target:
            candidatos.append((k, url))

    if candidatos:
        candidatos.sort(key=lambda kv: len(kv[0]))
        return candidatos[0]

    return None, None


# =========================
# EXTRACCIÓN DE UNA PÁGINA (INCREMENTAL + DEDUP)
# =========================
def _titulo_desde_pod(pod) -> str:
    title_selectors = [
        "[data-testid='product-title']",
        "[data-testid='name']",
        "h2, h3",
        "p[class*='title'], span[class*='title']",
    ]
    for sel in title_selectors:
        for el in pod.find_elements(By.CSS_SELECTOR, sel):
            t = el.text.strip()
            if t:
                return t

    img_elem = pod.find_elements(By.CSS_SELECTOR, "img[id^='testId-pod-image'], img[alt]")
    if img_elem:
        alt = (img_elem[0].get_attribute("alt") or "").strip()
        if alt:
            return alt
    return "N/A"


def extraer_productos_pagina(
    driver,
    contador_inicio=1,
    pagina_actual=1,
    categoria_actual="N/A",
    obtener_detalles=False,
    vistos_links: Optional[Set[str]] = None
) -> Tuple[List[Producto], int]:
    """
    Guarda cada producto en JSONL apenas se crea, evitando duplicados con 'vistos_links'.
    """
    global _EXTRACCION_TOTAL
    if vistos_links is None:
        vistos_links = set()

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

            if link in vistos_links:
                continue

            titulo = _titulo_desde_pod(pod)
            img_elem = pod.find_elements(By.CSS_SELECTOR, "img[id^='testId-pod-image']")
            imagen = img_elem[0].get_attribute("src") if img_elem and img_elem[0].get_attribute("src") else "N/A"

            if titulo == "N/A":
                child_texts = [e.text.strip() for e in pod.find_elements(By.CSS_SELECTOR, "*") if e.text.strip()]
                if child_texts:
                    titulo = child_texts[0]

            if titulo != "N/A" and PROMO_TITLE_PAT.search(titulo):
                continue
            if titulo != "N/A" and TITLE_EXCLUDE_PAT.search(titulo):
                continue

            marca = parsear_marca_desde_titulo(titulo) if titulo not in ("", "N/A") else "N/A"
            precio_txt, precio_num, moneda = extraer_precio_listado(pod)
            tamano = extraer_tamano_desde_titulo(titulo) if titulo not in ("", "N/A") else "N/A"
            calificacion = extraer_calificacion_listado(pod)
            detalles_adicionales = ""

            if obtener_detalles or calificacion in {"N/A", "", "0"}:
                original_window = driver.current_window_handle
                driver.execute_script("window.open(arguments[0]);", link)
                driver.switch_to.window(driver.window_handles[-1])
                try:
                    WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.TAGNAME, "body")))
                except Exception:
                    WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
                try:
                    nap(0.6, 1.0) if FAST_MODE else nap(1.0, 2.0)
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
                contador_extraccion_total=_EXTRACCION_TOTAL,
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

            if producto.precio_valor is None and (PROMO_TITLE_PAT.search(producto.titulo or "") or TITLE_EXCLUDE_PAT.search(producto.titulo or "")):
                continue

            # Incremental inmediato hacia el archivo de la corrida (RUN_JSONL)
            append_jsonl(producto)
            vistos_links.add(link)

            productos.append(producto)
            contador += 1
            _EXTRACCION_TOTAL += 1

            nap(0.15, 0.4) if FAST_MODE else nap(0.25, 0.7)

        except Exception as e:
            LOGGER.debug(f"[{categoria_actual}] Error en pod {i} de página {pagina_actual}: {e}")

    return productos, contador


# =========================
# NAVEGACIÓN PAGINACIÓN
# =========================
def ir_a_siguiente_pagina(driver) -> bool:
    try:
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        nap(0.5, 1.0) if FAST_MODE else nap(0.8, 1.8)

        posibles_botones = driver.find_elements(By.CSS_SELECTOR, "button[id*='pagination'][id*='arrow-right']")
        if not posibles_botones:
            posibles_botones = driver.find_elements(By.CSS_SELECTOR, "button.btn.pagination-arrow")
        if not posibles_botones:
            posibles_botones = driver.find_elements(By.CSS_SELECTOR, "li[class*='pagination-arrow'] a, a[class*='pagination-arrow']")
        if not posibles_botones:
            posibles_botones = driver.find_elements(By.CSS_SELECTOR, "a[rel='next'], button[aria-label*='Siguiente' i], a[aria-label*='Siguiente' i]")

        if not posibles_botones:
            return False

        siguiente_btn = posibles_botones[-1]
        pods = driver.find_elements(By.CSS_SELECTOR, "#testId-searchResults-products a[data-pod='catalyst-pod']")
        primer_pod = pods[0] if pods else None
        old_url = driver.current_url

        try:
            driver.execute_script("arguments[0].click();", siguiente_btn)
        except ElementClickInterceptedException:
            nap(0.3, 0.7) if FAST_MODE else nap(0.6, 1.2)
            driver.execute_script("arguments[0].click();", siguiente_btn)

        try:
            if primer_pod:
                WebDriverWait(driver, 15).until(EC.staleness_of(primer_pod))
            WebDriverWait(driver, 10).until(lambda d: d.current_url != old_url)
        except Exception:
            pass

        nap(0.5, 1.0) if FAST_MODE else nap(0.8, 1.6)
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

    # Red/TLS más tolerante
    options.add_argument("--ignore-certificate-errors")
    options.add_argument("--allow-insecure-localhost")
    options.add_argument("--disable-features=ClientHints,OptimizationHints,InterestCohort,PrivacySandboxAdsApis")
    options.add_argument("--disable-background-networking")

    # Evitar WebGL en headless
    options.add_argument("--disable-3d-apis")
    options.add_argument("--disable-webgl")
    options.add_argument("--disable-software-rasterizer")

    options.add_experimental_option("excludeSwitches", ["enable-automation", "enable-logging"])
    options.add_experimental_option("useAutomationExtension", False)

    proxy = os.getenv("HTTPS_PROXY") or os.getenv("HTTP_PROXY")
    if proxy:
        options.add_argument(f"--proxy-server={proxy}")

    from selenium.webdriver.common.desired_capabilities import DesiredCapabilities
    caps = DesiredCapabilities.CHROME.copy()
    caps["acceptInsecureCerts"] = True
    for key, value in caps.items():
        options.set_capability(key, value)

    # Carga más ágil
    options.set_capability("pageLoadStrategy", "eager")

    remote_url = os.getenv("SELENIUM_REMOTE_URL")
    if remote_url:
        driver = webdriver.Remote(command_executor=remote_url, options=options)
    else:
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=options)

    driver.set_page_load_timeout(90)
    driver.set_script_timeout(90)
    return driver


# =========================
# EXTRACCIÓN POR CATEGORÍA
# =========================
def extraer_categoria(
    driver,
    url_categoria: str,
    nombre_categoria: Optional[str] = None,
    limit_one_page: bool = False,
    max_pages: Optional[int] = None  # límite de páginas
) -> List[Producto]:
    safe_get(driver, url_categoria)
    WebDriverWait(driver, 15).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
    nap(1.0, 1.6) if FAST_MODE else nap(1.2, 2.0)

    # El nombre que se guarda dentro del objeto es el detectado en la página
    # (pero los archivos de salida ya usan la clave con set_run_outputs)
    categoria_nombre = obtener_nombre_categoria(driver) or nombre_categoria or "N/A"
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
            obtener_detalles=(not FAST_MODE),
            vistos_links=vistos
        )

        if not productos:
            LOGGER.info(f"[{categoria_nombre}] No hay nuevos productos en esta página. Fin.")
            break

        productos_totales.extend(productos)

        # 1) Si está activado el modo 1 página
        if limit_one_page:
            LOGGER.info(f"[{categoria_nombre}] Modo 1 página por categoría: detenido en página {pagina}.")
            break

        # 2) Si el usuario indicó máximo de páginas
        if max_pages is not None and pagina >= max_pages:
            LOGGER.info(f"[{categoria_nombre}] Alcanzado límite de {max_pages} páginas. Detenido en página {pagina}.")
            break

        # 3) Intentar pasar a la siguiente página
        if not ir_a_siguiente_pagina(driver):
            LOGGER.info(f"[{categoria_nombre}] No hay más páginas.")
            break

        pagina += 1
        nap(0.6, 1.2) if FAST_MODE else nap(1.0, 2.0)

    return productos_totales


# =========================
# EJECUCIÓN COMPLETA (por EXPECTED_URLS)
# =========================
def extraer_todas_categorias(max_pages: Optional[int] = None):
    """
    Scrapea TODAS las categorías definidas en EXPECTED_URLS.
    Crea un archivo por categoría {clave}_formatted.json / .jsonl
    """
    driver = crear_driver()
    try:
        items = list(EXPECTED_URLS.items())
        if isinstance(MAX_CATEGORIES, int) and MAX_CATEGORIES > 0:
            items = items[:MAX_CATEGORIES]

        for nombre, url in items:
            try:
                # Define archivos de salida para esta categoría por su clave
                set_run_outputs(nombre)
                productos_cat = extraer_categoria(
                    driver,
                    url,
                    nombre_categoria=nombre,
                    limit_one_page=LIMIT_ONE_PAGE_PER_CATEGORY,
                    max_pages=max_pages
                )
                # Guardado final (incremental ya se hizo)
                guardar_json(productos_cat, RUN_JSON)
                LOGGER.info(f"[{nombre}] Guardados {len(productos_cat)} productos en {RUN_JSON} y {RUN_JSONL}.")
            except Exception as e:
                LOGGER.warning(f"Error extrayendo categoría '{nombre}': {e}")
                continue

        LOGGER.info("✅ Proceso de scraping múltiple finalizado.")
    finally:
        driver.quit()


# Diccionario estático de categorías (nombre -> URL)
EXPECTED_URLS: Dict[str, str] = {
    "televisores": "https://www.falabella.com.co/falabella-co/category/cat1360967/TV-y-Video",
    "celulares": "https://www.falabella.com.co/falabella-co/category/cat1660941/Celulares-y-Telefonos",
    "laptops": "https://www.falabella.com.co/falabella-co/category/cat111222/laptops",
    "domotica": "https://www.falabella.com.co/falabella-co/category/cat10431000/Smart-Home",
    "lavado": "https://www.falabella.com.co/falabella-co/category/cat50714/Lavado",
    "refrigeracion": "https://www.falabella.com.co/falabella-co/category/CATG32130/Refrigeracion",
    "cocina": "https://www.falabella.com.co/falabella-co/category/cat2970970/Cocina",
    "audifonos": "https://www.falabella.com.co/falabella-co/category/cat50670/Audifonos",
    "videojuegos": "https://www.falabella.com.co/falabella-co/category/cat50590/Gaming",
    "deportes": "https://www.falabella.com.co/falabella-co/category/cat50620/Fitness-y-Gimnasio-en-casa",
}


# =========================
# MAIN
# =========================
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Scraper Falabella (incremental + modo rápido).")
    parser.add_argument(
        "--max-categories",
        type=int,
        default=None,
        help="Limitar cantidad de categorías cuando se scrapean todas (None = todas). Ej: --max-categories 5"
    )
    grp = parser.add_mutually_exclusive_group()
    grp.add_argument(
        "--one-page",
        dest="one_page",
        action="store_true",
        help="Limitar a 1 página por categoría (modo rápido)"
    )
    grp.add_argument(
        "--multi-page",
        dest="one_page",
        action="store_false",
        help="Permitir múltiples páginas por categoría"
    )
    parser.set_defaults(one_page=None)

    parser.add_argument(
        "--category",
        type=str,
        default=None,
        help="Nombre de la categoría a scrapear (según EXPECTED_URLS). Si se omite, se scrapean todas."
    )
    parser.add_argument(
        "--pages",
        type=int,
        default=None,
        help="Cantidad máxima de páginas a scrapear por categoría. Ej: --pages 3"
    )
    parser.add_argument(
        "--fast",
        action="store_true",
        help="Modo rápido: no abre todas las fichas (solo si falta rating) y reduce scroll/esperas."
    )

    args = parser.parse_args()

    # Overrides de configuración global según CLI
    if args.max_categories is not None:
        MAX_CATEGORIES = args.max_categories
    if args.one_page is not None:
        LIMIT_ONE_PAGE_PER_CATEGORY = bool(args.one_page)
    if args.fast:
        FAST_MODE = True
        LOGGER.info("⚡ Modo rápido activado (--fast)")

    # Si el usuario especifica una categoría
    if args.category:
        nombre_match, url = resolver_categoria_por_nombre(args.category)
        if not url:
            disponibles = ", ".join(sorted(EXPECTED_URLS.keys())) or "(vacío; agrega pares nombre->url en EXPECTED_URLS)"
            raise SystemExit(
                f"❌ No se encontró la categoría '{args.category}' en EXPECTED_URLS.\n"
                f"   Disponibles: {disponibles}"
            )

        # Define archivos por la clave elegida (asegura p.ej. 'celulares_formatted.json')
        set_run_outputs(nombre_match)

        driver = crear_driver()
        try:
            productos = extraer_categoria(
                driver,
                url,
                nombre_categoria=nombre_match,  # nombre guardado en el objeto
                limit_one_page=LIMIT_ONE_PAGE_PER_CATEGORY,
                max_pages=args.pages
            )
            # Guardado final (incremental ya se hizo a RUN_JSONL)
            guardar_json(productos, RUN_JSON)
            LOGGER.info(f"Guardados {len(productos)} productos en {RUN_JSON} y {RUN_JSONL}.")
        finally:
            driver.quit()
    else:
        # Sin categoría específica -> scrapea todas las de EXPECTED_URLS
        extraer_todas_categorias(max_pages=args.pages)
