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
import re
from urllib.parse import urljoin

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


# --- Endpoint de la API ---
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
        
        publicaciones = scrape_dof_publications(url_dof, nombre_depto)
        
        if not publicaciones:
            html_final_parts.append('<p><em>No se encontraron publicaciones para hoy.</em></p>')
            continue

        titulos_para_prompt = [pub['title'] for pub in publicaciones]
        texto_a_resumir = "\n".join(f"- {titulo}" for titulo in titulos_para_prompt)
        
        tipo_de_cambio_str = None
        if nombre_depto == 'BANCO DE MEXICO':
            print("Buscando publicación de tipo de cambio para BANCO DE MEXICO...")
            for pub in publicaciones:
                if 'tipo de cambio para solventar obligaciones' in pub['title'].lower():
                    try:
                        print(f"Encontrado enlace de tipo de cambio: {pub['url']}")
                        response_tc = requests.get(pub['url'], headers={'User-Agent': 'Mozilla/5.0'}, verify=False, timeout=10)
                        response_tc.raise_for_status()
                        soup_tc = BeautifulSoup(response_tc.text, 'html.parser')
                        
                        detalle_div = soup_tc.find('div', id='DivDetalleNota')
                        if detalle_div:
                            texto_completo = detalle_div.get_text(" ", strip=True)
                            match = re.search(r'el tipo de cambio obtenido el día de hoy fue de\s*(\$\s*\d+\.\d+\s*M\.N\.)', texto_completo, re.IGNORECASE)
                            if match:
                                # Usamos   para el espacio en HTML
                                tipo_de_cambio_str = match.group(1).replace(' ', ' ')
                                print(f"¡ÉXITO! Tipo de cambio extraído: {tipo_de_cambio_str}")
                                break
                            else:
                                print("FALLO DE EXTRACCIÓN: Se encontró el contenedor, pero el patrón de texto del tipo de cambio no coincidió.")
                        else:
                            print("FALLO DE SCRAPING: No se pudo encontrar el contenedor <div id='DivDetalleNota'> en la página.")
                    except Exception as e:
                        print(f"ERROR INESPERADO al procesar la página del tipo de cambio: {e}")

        # --- CAMBIO PRINCIPAL: INSTRUCCIONES DEL PROMPT ---
        # Se eliminó la regla de "Agrupar por Tema" y se reemplazó por una regla de "Un Punto por Publicación".
        prompt_usuario = f"""
        Tu tarea es analizar la siguiente lista de títulos de publicaciones del Diario Oficial de la Federación y generar un resumen ejecutivo.
        Sigue estas reglas ESTRICTAMENTE:
        1.  **Formato de Salida**: Tu respuesta debe ser ÚNICAMENTE una lista simple de puntos (bullet points) en formato Markdown. NO uses sub-puntos ni agrupes temas con encabezados en negrita.
        2.  **Un Punto por Publicación**: Por CADA título de la lista, crea un único bullet point (`-`). Cada bullet debe explicar de forma clara y concisa el propósito de esa publicación.
        3.  **Sin Introducción ni Conclusión**: Tu respuesta debe empezar directamente con el primer bullet point.
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
            # --- CAMBIO PRINCIPAL: PROCESAMIENTO SIMPLIFICADO ---
            # Ya no necesitamos reestructurar el HTML, solo lo convertimos y lo usamos.
            resumen_html = markdown.markdown(resumen_markdown, extensions=['fenced_code'])

            # Inyectamos el tipo de cambio si fue encontrado
            if tipo_de_cambio_str:
                print("Intentando inyectar el tipo de cambio en el resumen HTML...")
                # Usamos BeautifulSoup para modificar el HTML generado
                soup_depto = BeautifulSoup(resumen_html, 'html.parser')
                # Buscamos el <li> que hable del tipo de cambio
                li_tc = soup_depto.find(lambda tag: tag.name == 'li' and 'tipo de cambio' in tag.get_text(strip=True).lower())

                if li_tc:
                    # Añadimos el valor entre paréntesis al final de ese <li>
                    li_tc.append(f" ({tipo_de_cambio_str})")
                    resumen_html = str(soup_depto) # Actualizamos el HTML con la modificación
                    print("Inyección del tipo de cambio en el bullet point exitosa.")
                else:
                    # Si por alguna razón la IA no crea el bullet, lo añadimos al final como respaldo.
                    print("No se encontró el bullet point específico. Añadiendo el tipo de cambio al final de la sección.")
                    resumen_html += f'<p><em>(Tipo de cambio para solventar obligaciones: {tipo_de_cambio_str})</em></p>'
            
            html_final_parts.append(resumen_html)

        except Exception as e:
            print(f"Error en la API de OpenAI para '{nombre_depto}': {e}")
            html_final_parts.append("<p><em>Ocurrió un error al generar el resumen de IA.</em></p>")

    html_fragment = ''.join(html_final_parts)
    
    return Response(html_fragment, mimetype='text/html; charset=utf-8')


# --- Ejecutar la aplicación ---
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
