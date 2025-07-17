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
import re  # Añadido para expresiones regulares
from urllib.parse import urljoin # Añadido para construir URLs completas

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

# --- Lógica de Scraping (modificada para devolver URLs) ---
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
                # MODIFICACIÓN: Guardar título y URL completa
                if nombre_secretaria and texto_publicacion:
                    url_publicacion = link['href']
                    full_url = urljoin(url, url_publicacion)
                    publicaciones_por_secretaria[nombre_secretaria].append({
                        "title": texto_publicacion,
                        "url": full_url
                    })
        
        department_publications = publicaciones_por_secretaria.get(department_name, [])
        print(f"Se encontraron {len(department_publications)} publicaciones para '{department_name}'.")
        return department_publications
    except requests.exceptions.RequestException as e:
        print(f"Error de red durante el scraping para '{department_name}': {e}")
        return []
    except Exception as e:
        print(f"Error inesperado de scraping para '{department_name}': {e}")
        return []


# --- Endpoint de la API (modificado para extraer y añadir tipo de cambio) ---
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
        
        html_final_parts.append(f'<h2>{nombre_depto}</h2>')
        
        # scrape_dof_publications ahora devuelve una lista de diccionarios
        publicaciones = scrape_dof_publications(url_dof, nombre_depto)
        
        if not publicaciones:
            html_final_parts.append('<p><em>No se encontraron publicaciones para hoy.</em></p>')
            continue

        # Extraer solo los títulos para el prompt de la IA
        titulos_para_prompt = [pub['title'] for pub in publicaciones]
        texto_a_resumir = "\n".join(f"- {titulo}" for titulo in titulos_para_prompt)
        
        # --- NUEVA LÓGICA PARA EXTRAER TIPO DE CAMBIO ---
        tipo_de_cambio_str = None
        if nombre_depto == 'BANCO DE MEXICO':
            print("Buscando tipo de cambio para BANCO DE MEXICO...")
            for pub in publicaciones:
                if 'tipo de cambio' in pub['title'].lower():
                    try:
                        print(f"Encontrado enlace de tipo de cambio: {pub['url']}")
                        response_tc = requests.get(pub['url'], headers={'User-Agent': 'Mozilla/5.0'}, verify=False, timeout=10)
                        response_tc.raise_for_status()
                        soup_tc = BeautifulSoup(response_tc.text, 'html.parser')
                        
                        contenido_td = soup_tc.find('td', class_='texto')
                        if contenido_td:
                            texto_completo = contenido_td.get_text()
                            if 'el tipo de cambio obtenido el día de hoy fue de' in texto_completo:
                                match = re.search(r'(\$\d+\.\d+\s*M\.N\.)', texto_completo)
                                if match:
                                    # Usar non-breaking space para mejor visualización en HTML
                                    tipo_de_cambio_str = match.group(1).replace(' ', ' ')
                                    print(f"Tipo de cambio extraído: {tipo_de_cambio_str}")
                                    break # Dejar de buscar una vez encontrado
                    except Exception as e:
                        print(f"No se pudo extraer el tipo de cambio de {pub['url']}: {e}")

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
        
        print(f"Enviando {len(titulos_para_prompt)} títulos a OpenAI para resumir...")
        
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
                    reestructurado_fragments.append(f'<p><strong>{strong_child.get_text(strip=True)}</strong></p>')
                else:
                    current_sub_list.append(f"<li>{li.decode_contents()}</li>")

            if current_sub_list:
                reestructurado_fragments.append(f"<ul>{''.join(current_sub_list)}</ul>")
                
            html_depto_reestructurado = "".join(reestructurado_fragments)
            html_final_parts.append(html_depto_reestructurado)
            
            # --- MODIFICACIÓN: Añadir el tipo de cambio al final del resumen de BANXICO ---
            if tipo_de_cambio_str:
                html_final_parts.append(f'<p><em>(Tipo de cambio: {tipo_de_cambio_str})</em></p>')

        except Exception as e:
            print(f"Error en la API de OpenAI para '{nombre_depto}': {e}")
            html_final_parts.append("<p><em>Ocurrió un error al generar el resumen de IA.</em></p>")

    html_fragment = ''.join(html_final_parts)
    
    return Response(html_fragment, mimetype='text/html; charset=utf-8')


# --- Ejecutar la aplicación ---
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
