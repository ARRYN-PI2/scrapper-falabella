📦 Instalación

Clona el repositorio y entra en la carpeta:

git clone https://github.com/tu_usuario/scrapper-falabella.git
cd scrapper-falabella


Crea un entorno virtual (opcional, recomendado):

python -m venv venv
source venv/bin/activate      # En Linux / Mac
venv\Scripts\activate         # En Windows


Instala las dependencias desde requirements.txt:

pip install -r requirements.txt

▶️ Ejecución básica

Para ejecutar el scraper con la configuración por defecto:

python scrape_falabella_all.py


Esto recorrerá todas las categorías detectadas en Falabella, y por defecto solo 1 página por categoría.

⚙️ Opciones de línea de comandos

El scraper soporta opciones para limitar categorías y páginas:

Limitar número de categorías (ejemplo: solo 5):

python scrape_falabella_all.py --max-categories 5


Forzar 1 sola página por categoría (modo rápido):

python scrape_falabella_all.py --one-page


Permitir múltiples páginas por categoría:

python scrape_falabella_all.py --multi-page


Ejemplo combinado:

python scrape_falabella_all.py --max-categories 3 --multi-page

📂 Archivos generados

out/productos_all.json → lista acumulada de todos los productos.

out/productos_all.jsonl → guardado incremental, un producto por línea.