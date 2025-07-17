# --- Endpoint de la API (con la corrección final y depuración mejorada) ---
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
                        
                        # --- CORRECCIÓN CLAVE ---
                        # 1. Buscamos el div por su ID, que es más fiable.
                        print("DEBUG: Buscando el contenedor de la nota (div id='DivDetalleNota')...")
                        detalle_div = soup_tc.find('div', id='DivDetalleNota')

                        if detalle_div:
                            print("DEBUG: Contenedor encontrado. Extrayendo todo el texto...")
                            # 2. Extraemos todo el texto del div, ignorando las etiquetas internas.
                            texto_completo = detalle_div.get_text(" ", strip=True)
                            
                            # Imprimimos los primeros caracteres para verificar
                            print(f"DEBUG: Texto extraído (inicio): '{texto_completo[:200]}...'")

                            # 3. Buscamos el patrón en el texto extraído.
                            print("DEBUG: Aplicando expresión regular para encontrar el valor del TC...")
                            match = re.search(r'el tipo de cambio obtenido el día de hoy fue de\s*(\$\s*\d+\.\d+\s*M\.N\.)', texto_completo, re.IGNORECASE)
                            
                            if match:
                                tipo_de_cambio_str = match.group(1).replace(' ', ' ')
                                print(f"¡ÉXITO! Tipo de cambio extraído: {tipo_de_cambio_str}")
                                break # Salimos del bucle porque ya lo encontramos
                            else:
                                print("FALLO DE EXTRACCIÓN: Se encontró el contenedor, pero el patrón de texto del tipo de cambio no coincidió.")
                        else:
                            print("FALLO DE SCRAPING: No se pudo encontrar el contenedor <div id='DivDetalleNota'> en la página.")
                    except Exception as e:
                        print(f"ERROR INESPERADO al procesar la página del tipo de cambio: {e}")

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

            if tipo_de_cambio_str:
                print("Intentando inyectar el tipo de cambio en el resumen HTML...")
                soup_depto = BeautifulSoup(html_depto_reestructurado, 'html.parser')
                li_tc = soup_depto.find(lambda tag: tag.name == 'li' and 'tipo de cambio' in tag.get_text(strip=True).lower())

                if li_tc:
                    li_tc.append(f" ({tipo_de_cambio_str})")
                    html_depto_reestructurado = str(soup_depto)
                    print("Inyección del tipo de cambio en el bullet point exitosa.")
                else:
                    print("No se encontró el bullet point específico. Añadiendo el tipo de cambio al final de la sección.")
                    html_depto_reestructurado += f'<p><em>(Tipo de cambio para solventar obligaciones: {tipo_de_cambio_str})</em></p>'
            
            html_final_parts.append(html_depto_reestructurado)

        except Exception as e:
            print(f"Error en la API de OpenAI para '{nombre_depto}': {e}")
            html_final_parts.append("<p><em>Ocurrió un error al generar el resumen de IA.</em></p>")

    html_fragment = ''.join(html_final_parts)
    
    return Response(html_fragment, mimetype='text/html; charset=utf-8')
