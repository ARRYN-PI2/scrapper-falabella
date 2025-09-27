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


🎯 Ejecución por categorías específicas

El scrapper soporta ejecución filtrada por categorías usando el flag --category.
Estas son las categorías disponibles:

📺 Televisores
python scrape_falabella_all.py --category "televisores"

📱 Celulares
python scrape_falabella_all.py --category "celulares"

💻 Laptops
python scrape_falabella_all.py --category "laptops"

🏠 Domótica
python scrape_falabella_all.py --category "domotica"

🧺 Lavado
python scrape_falabella_all.py --category "lavado"

❄️ Refrigeración
python scrape_falabella_all.py --category "refrigeracion"

🍳 Cocina
python scrape_falabella_all.py --category "cocina"

🎧 Audífonos
python scrape_falabella_all.py --category "audifonos"

🎮 Videojuegos
python scrape_falabella_all.py --category "videojuegos"

🏋️ Deportes 
python scrape_falabella_all.py --category "deportes"

📂 Archivos generados

out/productos_all.json → lista acumulada de todos los productos.

out/productos_all.jsonl → guardado incremental, un producto por línea.