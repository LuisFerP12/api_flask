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


# --- Endpoint de la API (con la nueva lógica de reestructuración de HTML) ---
@app.route('/resumir-hacienda', methods=['GET'])
def resumir_hacienda():
    secretaria = 'SECRETARIA DE HACIENDA Y CREDITO PUBLICO'
    url_dof = 'https://www.dof.gob.mx/'
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
        
        resumen_html_body = markdown.markdown(resumen_markdown, extensions=['fenced_code'])
        
        soup = BeautifulSoup(resumen_html_body, 'html.parser')
        ul_tag = soup.find('ul')
        
        if not ul_tag:
            return Response(resumen_html_body, mimetype='text/html; charset=utf-8')

        html_fragments = []
        current_sub_list = []

        for li in ul_tag.find_all('li', recursive=False):
            strong_child = li.find('strong')
            is_header = strong_child and li.get_text(strip=True) == strong_child.get_text(strip=True)

            if is_header:
                if current_sub_list:
                    html_fragments.append(f"<ul>{''.join(current_sub_list)}</ul>")
                    current_sub_list = []

                # --- INICIO DEL CAMBIO ---
                # Se aumenta el padding a 40px para igualar la sangría estándar de las listas <ul>.
                # Un párrafo <p> no tiene sangría por defecto, por eso la añadimos manualmente.
                html_fragments.append(f'<p style="margin-left: 20px;">{str(strong_child)}</p>')
                # --- FIN DEL CAMBIO ---

            else:
                current_sub_list.append(str(li))

        if current_sub_list:
            html_fragments.append(f"<ul>{''.join(current_sub_list)}</ul>")
            
        html_final = "".join(html_fragments)

        return Response(html_final, mimetype='text/html; charset=utf-8')

    except Exception as e:
        print(f"Error en la API: {e}")
        return Response("Ocurrió un error al procesar la solicitud de IA.", status=500, mimetype='text/plain')

# --- Ejecutar la aplicación ---
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
