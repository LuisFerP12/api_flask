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
# Asegúrate de tener tu clave de API de OpenAI en una variable de entorno.
# En tu terminal, ejecuta: export OPENAI_API_KEY='tu_clave_aqui'
API_KEY = os.environ.get("OPENAI_API_KEY")

# --- Configuración ---
app = Flask(__name__)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

if not API_KEY:
    print("\nERROR: La variable de entorno OPENAI_API_KEY no está configurada.")
    # Si no hay clave, la aplicación no funcionará, así que es mejor salir.
    # exit() # Descomenta si prefieres que el script se detenga aquí.
    client = None # Evitar que falle al inicializar
else:
    client = OpenAI(api_key=API_KEY)


# --- Lógica de Scraping (sin cambios) ---
def scrape_dof_publications(url: str, department_name: str) -> list:
    """
    Realiza scraping en la página principal del DOF para obtener los títulos de las publicaciones
    de una secretaría específica.
    """
    print(f"Iniciando scraping para '{department_name}' en {url}")
    try:
        response = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, verify=False, timeout=15)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        publicaciones_por_secretaria = defaultdict(list)
        all_publications_links = soup.find_all('a', href=lambda href: href and 'nota_detalle.php' in href)
        if not all_publications_links:
            print("No se encontraron enlaces de publicaciones.")
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
        return publicaciones_por_secretaria.get(department_name, [])
    except Exception as e:
        print(f"Error durante el scraping: {e}")
        return []


# --- Endpoint de la API (con la lógica de CSS para estilizar) ---
@app.route('/resumir-hacienda', methods=['GET'])
def resumir_hacienda():
    if not client:
        return Response("Error: La clave de API de OpenAI no está configurada.", status=500, mimetype='text/plain')
        
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
    2.  **Sin Introducción ni Conclusión**: Tu respuesta debe empezar directamente con el primer bullet point (`-`). No incluyas frases como "Aquí está el resumen:".
    3.  **Agrupa por Tema**: Agrupa los títulos relacionados bajo un punto principal en negrita y luego detalla con sub-puntos con sangría. Por ejemplo:
        - **Tema Principal 1**
          - Detalle A sobre el tema 1.
          - Detalle B sobre el tema 1.
        - **Tema Principal 2**
          - Detalle C sobre el tema 2.
    4.  **Lenguaje Claro**: Explica cada punto de forma clara y concisa.
    
    Ahora, genera el resumen para la siguiente lista de publicaciones:
    {texto_a_resumir}
    """
    
    print("Enviando solicitud a OpenAI para resumir...")
    
    try:
        completion = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "Eres un asistente experto en analizar documentos gubernamentales y generar resúmenes ejecutivos en formato Markdown."},
                {"role": "user", "content": prompt_usuario}
            ]
        )
        
        resumen_markdown = completion.choices[0].message.content
        
        # 1. Convertimos el Markdown a un fragmento de HTML.
        #    Esto creará una estructura de listas anidadas (ul > li > ul > li) que es perfecta.
        resumen_html_body = markdown.markdown(resumen_markdown, extensions=['fenced_code'])
        
        # --- INICIO DE LA NUEVA LÓGICA CON CSS ---
        
        # 2. Definimos el CSS que se inyectará en el HTML para dar estilo.
        css_styles = """
        <style>
            body { 
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
                line-height: 1.6;
                margin: 2em;
            }
            /* Seleccionamos SÓLO los <li> que son hijos directos del <ul> principal */
            /* Estos son nuestros encabezados temáticos */
            body > ul > li {
                list-style-type: none; /* Quitamos el bullet point del encabezado */
                margin-left: -20px;    /* Compensamos la sangría para alinear el texto en negrita */
                margin-bottom: 1em;    /* Añadimos un espacio vertical entre grupos para mayor claridad */
            }
            /* Damos un poco de espacio entre el encabezado y su lista de sub-puntos */
            body > ul > li > ul {
                margin-top: 0.5em;
            }
        </style>
        """
        
        # 3. Creamos un documento HTML completo, inyectando nuestros estilos y el contenido.
        html_final = f"""
        <!DOCTYPE html>
        <html lang="es">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Resumen del DOF - Secretaría de Hacienda</title>
            {css_styles}
        </head>
        <body>
            {resumen_html_body}
        </body>
        </html>
        """
        # --- FIN DE LA NUEVA LÓGICA ---

        return Response(html_final, mimetype='text/html; charset=utf-8')

    except Exception as e:
        print(f"Error en la API de OpenAI: {e}")
        return Response("Ocurrió un error al procesar la solicitud de IA.", status=500, mimetype='text/plain')

# --- Ejecutar la aplicación ---
if __name__ == '__main__':
    # Usar el puerto 5001 es común para desarrollo local si el 5000 está ocupado.
    app.run(host='0.0.0.0', port=5001, debug=True)
