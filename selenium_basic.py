import requests
import json

# URL directa del XHR
url = 'https://www.falabella.com/s/browse/v2/recommended-products/cl?pageType=LANDING&slots=HOME-HRT-41&politicalId=15c37b0b-a392-41a9-8b3b-978376c700d5&priceGroupId=96&channel=web'

# Headers opcionales
headers = {
    'User-Agent': 'Mozilla/5.0',
    'Accept': 'application/json',
}

# Hacer la solicituds
response = requests.get(url, headers=headers)

# Verificar estado
if response.status_code == 200:
    data = response.json()  
    
    with open('falabella_response.json', 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=4)
else:
    print(f"Error al hacer la solicitud: {response.status_code}")
