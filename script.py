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
    client = None
else:
    client = OpenAI(api_key=API_KEY)

# --- Lógica de Scraping (sin cambios) ---
def scrape_dof_publications(url: str, department_name: str) -> list:
    print(f"Iniciando scraping para '{department_name}' en {url}")
    try:
        response = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, verify=False, timeout=20)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        publicaciones_por_secretaria = defaultdict(list)
        all_publications_links = soup.find_all('a', href=lambda href: href and 'nota_detalle.php' in href)
        if not all_publications_links:
            print(f"No se encontraron enlaces de publicaciones en {url}")
            return []
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
        
        department_publications = publicaciones_por_secretaria.get(department_name, [])
        print(f"Se encontraron {len(department_publications)} publicaciones para '{department_name}'.")
        return department_publications
    except requests.exceptions.RequestException as e:
        print(f"Error de red durante el scraping para '{department_name}': {e}")
        return []
    except Exception as e:
        print(f"Error inesperado de scraping para '{department_name}': {e}")
        return []


# --- Endpoint de la API (modificado para generar solo el fragmento de HTML) ---
@app.route('/resumir-hacienda', methods=['GET'])
def resumir_hacienda():
    if not client:
         return Response("La clave de API de OpenAI no está configurada.", status=500, mimetype='text/plain')

    departamentos_a_procesar = [
        'SECRETARIA DE HACIENDA Y CREDITO PUBLICO',
        'BANCO DE MEXICO'
    ]
    url_dof = 'https://www.dof.gob.mx/'
    
    html_final_parts = []
    
    for nombre_depto in departamentos_a_procesar:
        print(f"--- Procesando: {nombre_depto} ---")
        
        # Genera el título del departamento que será estilizado por la plantilla de correo
        html_final_parts.append(f'<h2>{nombre_depto}</h2>')
        
        titulos = scrape_dof_publications(url_dof, nombre_depto)
        
        if not titulos:
            html_final_parts.append('<p><em>No se encontraron publicaciones para hoy.</em></p>')
            continue

        texto_a_resumir = "\n".join(f"- {titulo}" for titulo in titulos)
        prompt_usuario = f"""
        Tu tarea es analizar la siguiente lista de títulos de publicaciones del Diario Oficial de la Federación y generar un resumen ejecutivo.
        Sigue estas reglas ESTRICTAMENTE:
        1.  **Formato de Salida**: Tu respuesta debe ser ÚNICAMENTE una lista de puntos (bullet points) en formato Markdown.
        2.  **Sin Introducción ni Conclusión**: Tu respuesta debe empezar directamente con el primer bullet point (`-`).
        3.  **Agrupa por Tema**: Agrupa los títulos relacionados bajo un punto principal en negrita (`**Tema Principal**`) y luego detalla con sub-puntos. Los subpuntos NO deben estar en negrita.
        4.  **Lenguaje Claro**: Explica cada punto de forma clara y concisa.
        Ahora, genera el resumen para la siguiente lista de publicaciones:
        {texto_a_resumir}
        """
        
        print(f"Enviando {len(titulos)} títulos a OpenAI para resumir...")
        
        try:
            completion = client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": "Eres un asistente experto en analizar documentos gubernamentales y generar resúmenes ejecutivos claros en formato Markdown."},
                    {"role": "user", "content": prompt_usuario}
                ]
            )
            
            resumen_markdown = completion.choices[0].message.content
            resumen_html_body = markdown.markdown(resumen_markdown, extensions=['fenced_code'])
            
            soup = BeautifulSoup(resumen_html_body, 'html.parser')
            ul_tag = soup.find('ul')
            
            if not ul_tag:
                html_final_parts.append(resumen_html_body)
                continue

            reestructurado_fragments = []
            current_sub_list = []

            for li in ul_tag.find_all('li', recursive=False):
                strong_child = li.find('strong')
                is_header = strong_child and li.get_text(strip=True) == strong_child.get_text(strip=True)

                if is_header:
                    if current_sub_list:
                        reestructurado_fragments.append(f"<ul>{''.join(current_sub_list)}</ul>")
                        current_sub_list = []
                    # Transforma el encabezado en un párrafo con negritas, que será estilizado por la plantilla
                    reestructurado_fragments.append(f'<p><strong>{strong_child.get_text(strip=True)}</strong></p>')
                else:
                    current_sub_list.append(f"<li>{li.decode_contents()}</li>")

            if current_sub_list:
                reestructurado_fragments.append(f"<ul>{''.join(current_sub_list)}</ul>")
                
            html_depto_reestructurado = "".join(reestructurado_fragments)
            html_final_parts.append(html_depto_reestructurado)

        except Exception as e:
            print(f"Error en la API de OpenAI para '{nombre_depto}': {e}")
            html_final_parts.append("<p><em>Ocurrió un error al generar el resumen de IA.</em></p>")

    # Une todas las partes en un solo fragmento de HTML, sin estructura de página completa.
    html_fragment = ''.join(html_final_parts)
    
    return Response(html_fragment, mimetype='text/html; charset=utf-8')


# --- Ejecutar la aplicación ---
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
