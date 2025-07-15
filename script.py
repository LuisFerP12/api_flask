# --- Imports ---
import os
import requests
from bs4 import BeautifulSoup
import urllib3
import copy
from collections import defaultdict
from flask import Flask, Response
from openai import OpenAI
import markdown

# --- CONFIGURACIÓN SEGURA DE LA CLAVE DE API ---
API_KEY = os.environ.get("OPENAI_API_KEY")

# --- Configuración ---
app = Flask(__name__)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

if not API_KEY:
    print("\nERROR: La variable de entorno OPENAI_API_KEY no está configurada.")
client = OpenAI(api_key=API_KEY)


# --- Lógica de Scraping (sin cambios) ---
def scrape_dof_publications(url: str, department_name: str) -> list:
    # ... (esta función se queda exactamente igual) ...
    print(f"Iniciando scraping para '{department_name}' en {url}")
    try:
        response = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, verify=False, timeout=15)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        publicaciones_por_secretaria = defaultdict(list)
        all_publications_links = soup.find_all('a', href=lambda href: href and 'nota_detalle.php' in href)
        if not all_publications_links: return []
        for link in all_publications_links:
            parent_tr = link.find_parent('tr')
            if not parent_tr: continue
            title_tag = parent_tr.find_previous('td', class_='subtitle_azul')
            if title_tag:
                tag_copy = copy.copy(title_tag)
                link_a_eliminar = tag_copy.find('a')
                if link_a_eliminar: link_a_eliminar.decompose()
                nombre_secretaria = tag_copy.get_text(strip=True)
                texto_publicacion = link.get_text(strip=True)
                if nombre_secretaria and texto_publicacion:
                    publicaciones_por_secretaria[nombre_secretaria].append(texto_publicacion)
        return publicaciones_por_secretaria.get(department_name, [])
    except Exception as e:
        print(f"Error de scraping: {e}")
        return []


# --- Endpoint de la API (con la modificación) ---
@app.route('/resumir-hacienda', methods=['GET'])
def resumir_hacienda():
    secretaria = 'SECRETARIA DE HACIENDA Y CREDITO PUBLICO'
    url_dof = 'https://www.dof.gob.mx/' # Usando la URL principal para obtener lo de hoy
    titulos = scrape_dof_publications(url_dof, secretaria)
    if not titulos:
        return Response(f"No se encontraron publicaciones para '{secretaria}'.", status=404, mimetype='text/plain')

    texto_a_resumir = "\n".join(f"- {titulo}" for titulo in titulos)
    prompt_usuario = f"""
    Tu tarea es analizar la siguiente lista de títulos de publicaciones del Diario Oficial de la Federación y generar un resumen ejecutivo.
    Sigue estas reglas ESTRICTAMENTE:
    1.  **Formato de Salida**: Tu respuesta debe ser ÚNICAMENTE una lista de puntos (bullet points) en formato Markdown.
    2.  **Sin Introducción ni Conclusión**: Tu respuesta debe empezar directamente con el primer bullet point (`-`).
    3.  **Agrupa por Tema**: Agrupa los títulos relacionados bajo un punto principal en negrita y luego detalla con sub-puntos.
    4.  **Lenguaje Claro**: Explica cada punto de forma clara y concisa.
    Ahora, genera el resumen para la siguiente lista de publicaciones:
    {texto_a_resumir}
    """
    
    print("Enviando solicitud a OpenAI para resumir...")
    
    try:
        completion = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "Eres un asistente experto en analizar documentos gubernamentales."},
                {"role": "user", "content": prompt_usuario}
            ]
        )
        
        resumen_markdown = completion.choices[0].message.content
        
        # 1. Convertimos el Markdown a un fragmento de HTML (<ul>, <li>, etc.)
        resumen_html_body = markdown.markdown(resumen_markdown, extensions=['fenced_code'])
        
        # --- INICIO DE LA MODIFICACIÓN ---
        # El objetivo es quitar el "punto" de los <li> que son encabezados en negrita.
        
        # 2. Usamos BeautifulSoup para analizar este fragmento de HTML.
        #    Esto nos permite manipular el HTML de forma segura y precisa.
        soup_html = BeautifulSoup(resumen_html_body, 'html.parser')

        # 3. Buscamos todas las etiquetas <li>.
        for li_tag in soup_html.find_all('li'):
            # 4. Verificamos si el <li> contiene una etiqueta <strong> como hijo principal.
            #    La condición comprueba si el texto del <li> (sin espacios) es idéntico
            #    al texto de su hijo <strong> (sin espacios). Esto confirma que el <strong>
            #    es el único contenido textual del <li>, identificándolo como un encabezado.
            strong_child = li_tag.find('strong')
            if strong_child and li_tag.get_text(strip=True) == strong_child.get_text(strip=True):
                # 5. Si es un encabezado, le añadimos un estilo CSS para quitar el bullet point.
                #    'list-style-type: none;' oculta el punto.
                #    'margin-left: -1.5em;' compensa la sangría que el navegador añade
                #    por defecto a los elementos de lista, alineándolo con el resto.
                li_tag['style'] = 'list-style-type: none; margin-left: -1.5em;'
        
        # 6. Convertimos el objeto BeautifulSoup modificado de nuevo a un string de HTML.
        html_modificado = str(soup_html)
        # --- FIN DE LA MODIFICACIÓN ---

        # 7. Devolvemos el HTML modificado como respuesta.
        return Response(html_modificado, mimetype='text/html; charset=utf-8')

    except Exception as e:
        print(f"Error en la API: {e}")
        return Response("Ocurrió un error al procesar la solicitud de IA.", status=500, mimetype='text/plain')

# --- Ejecutar la aplicación ---
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
