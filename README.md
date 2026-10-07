# Huella · Observatorio local de contenido

Aplicación local para reunir evidencias visibles de TikTok y comparar sus temas a lo largo del tiempo. Trabaja con publicaciones del perfil, republicaciones y Me gusta públicos; conserva capturas, textos, metadatos y las limitaciones de cada muestra.

El informe describe **contenido observado**. Una frase de despedida, una canción o un repost no prueban una ruptura, un estado emocional ni una vivencia del titular. El proyecto no construye diagnósticos, perfiles psicológicos ni inferencias de atributos privados.

## Inicio en Windows

Requisitos: **Python 3.12**, Google Chrome instalado y acceso a Internet para visitar TikTok. El backend es FastAPI; la interfaz usa HTML, CSS y JavaScript sin compilación de frontend.

Desde PowerShell, en la carpeta del proyecto:

```powershell
.\start.ps1
```

El script crea `.venv` e instala `requirements.txt` si el entorno aún no existe. Después abre [Huella](http://127.0.0.1:8765) en el navegador. El servidor escucha en `127.0.0.1:8765`; `Ctrl+C` lo detiene.

Si prefieres preparar el entorno manualmente:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m app
```

Con [uv](https://docs.astral.sh/uv/pip/environments/) ya instalado:

```powershell
uv venv --python 3.12 .venv
uv pip install --python .\.venv\Scripts\python.exe -r requirements.txt
.\.venv\Scripts\python.exe -m app
```

No necesitas activar el entorno. Si `.venv` ya existe pero faltan paquetes, ejecuta otra vez el comando de instalación correspondiente. Un entorno creado por uv puede no incluir pip; en ese caso usa `uv pip install`.

`requirements.lock.txt` conserva las versiones del entorno validado. Para preparar un entorno con esas versiones, sustituye `requirements.txt` por `requirements.lock.txt` en el comando de instalación. El lock usa una sola variante de OpenCV: `opencv-python`.

Se utiliza el canal `chrome` de Playwright, que abre Chrome instalado. Instalar solamente el Chromium de Playwright no sustituye ese requisito. Consulta la [documentación de navegadores de Playwright](https://playwright.dev/python/docs/browsers#google-chrome--microsoft-edge).

## Primera captura

1. Inicia el servidor y abre el panel local.
2. Introduce el usuario, `@usuario` o el enlace real del perfil. Abre la sesión de Chrome desde el panel.
3. Completa **manualmente** el inicio de sesión en la ventana que se abre. El proyecto no solicita tu contraseña en su interfaz.
4. Selecciona publicaciones, compartidos y/o Me gusta. Comienza con una muestra pequeña para revisar la cobertura y los selectores.
5. Guarda el perfil e inicia la captura. «Capturar sin ventana» está activado por defecto. Revisa el estado de cada fuente y las notas de cada evidencia antes de interpretar el informe.
6. Corrige textos o fechas que puedas verificar, adjunta archivos locales si necesitas más contexto y vuelve a analizar. Puedes exportar JSON o Markdown.

La sesión usa un **perfil de Chrome dedicado y persistente**, guardado en `.browser-profile/`. La API no se conecta a tus pestañas actuales ni reutiliza automáticamente la sesión de tu Chrome habitual. Tras iniciar sesión una vez, Chrome puede conservarla en ese perfil; TikTok puede volver a pedir autenticación.

Una verificación, un límite de acceso o un perfil privado detienen la captura para revisión. Una pestaña ausente, privada o que no carga se registra como tal: no significa que el usuario nunca publicara, compartiera o marcara Me gusta. El colector no evade estas restricciones.

## Perfiles guardados y actualizaciones

Cada perfil conserva nombre, notas, foto, fuentes y límites de captura, además de un archivo acumulado de evidencia. La foto se intenta obtener del perfil público; si falla, se muestran iniciales y puedes subir una imagen. No se genera un rostro ficticio.

El modo **incremental** vuelve a leer las rejillas y procesa los elementos nuevos o pendientes; reutiliza OCR, imágenes y contexto ya guardados. No se detiene al primer elemento conocido porque los fijados y el orden pueden cambiar. El límite de elementos se aplica al trabajo nuevo, no a las filas reutilizadas. El modo **actualizar todo** fuerza una nueva extracción dentro de los límites elegidos.

Se conservan la primera y última observación, las correcciones manuales y la procedencia del informe original. La ausencia en una muestra nueva no borra registros anteriores ni demuestra que hayan sido eliminados. Los intentos parciales tienen una espera de seis horas antes del reintento automático; actualizar todo permite reintentar antes. Los fotogramas de la versión anterior, que no comprobaba superposiciones, se reparan una vez al volver a encontrarlos.

El archivo acumulado reúne contenido de todas las capturas. Los informes individuales permiten revisar cada ejecución y sus bloqueos. La fecha de primera observación sigue sin ser una fecha de repost. Las menciones conservan su texto, fuente y autoría cuando se puede verificar; una etiqueta no establece una relación entre personas.

La sesión visible se reserva para el acceso manual o una verificación. La captura headless usa el mismo perfil dedicado, pero la plataforma puede presentar resultados distintos según sesión, carga o modo de navegador. El diagnóstico conserva qué pestañas se detectaron y qué impidió continuar. La ayuda de navegación «Entendido» se cierra antes de capturar; un CAPTCHA requiere resolución manual.

## Interpretar imágenes con Gemini

En **Ajustes IA**, selecciona Gemini, introduce la API key en el campo de contraseña y guarda. La clave no se devuelve a la interfaz ni se incluye en informes. En Windows se cifra con DPAPI para el usuario actual; si no puede cifrarse, se conserva únicamente durante la sesión del servidor. También puedes configurar `GEMINI_API_KEY` en el entorno. Borrar una clave desde el panel no elimina una variable de entorno del proceso.

El botón de visión de una evidencia analiza sus imágenes recortadas junto con descripción, OCR, etiqueta musical y transcripción disponible. Activar análisis visual automático permite ejecutarlo durante las capturas, con un límite por ejecución (10 por defecto). **Este proveedor envía esas imágenes y textos a Google**. No envía la sesión ni las cookies de TikTok. Ollama permanece como alternativa local.

Gemini puede interpretar escenas y prendas aunque no contengan texto. El resultado separa observaciones, posibles referencias culturales, texto visible e incertidumbres. Una prenda puede guardar relación con un estilo regional; eso no acredita la etnia, procedencia o identidad de quien la lleva. La etiqueta de una canción aporta contexto, pero no equivale a haber escuchado su audio.

Se redimensionan y deduplican hasta cuatro imágenes por solicitud. Una caché por contenido, contexto y modelo evita repetir peticiones idénticas. Se registran solicitudes y tokens informados por el proveedor; no se inventa un coste en dólares. La disponibilidad y cuota dependen de tu cuenta. Consulta [visión con Gemini](https://ai.google.dev/gemini-api/docs/image-understanding) y [precios y uso de datos](https://ai.google.dev/gemini-api/docs/pricing); el nivel gratuito puede utilizar el contenido para mejorar productos.

Sin una clave o un modelo local configurado, el OCR sigue funcionando y el resumen indica que falta interpretación visual. Los errores de cuota o autenticación se muestran como bloqueos de IA y conservan la evidencia original.

## Qué recoge y qué puede faltar

| Evidencia | Implementación actual | Límite que hay que revisar |
| --- | --- | --- |
| Publicaciones, compartidos y Me gusta | Rejillas visibles, enlaces y fuente separados | Muestra acotada; una pestaña puede no estar disponible |
| Texto de publicación | Descripción visible o texto alternativo de miniatura | Puede truncarse o faltar según la página |
| Contexto visual del video | Hasta 3 capturas renderizadas por publicación desde el navegador | No equivale a conservar el video completo; escenas o texto fugaz pueden faltar |
| Imágenes y carruseles | Imagen visible o tarjeta recortada cuando puede aislarse sin obstáculos | No garantiza todas las diapositivas. Puedes adjuntar imágenes adicionales |
| Texto sobreimpreso | OCR local del original y variantes con contraste/recorte | Errores con tipografías, movimiento, idiomas, ironía o bajo contraste |
| Música | Etiqueta/enlace musical visible, con procedencia | No es reconocimiento de audio; no acredita la letra ni el significado personal |
| Hashtags y autor del contenido | Campos visibles guardados por registro | El autor del video y el perfil que lo republica pueden ser distintos |
| Fijados y posición de rejilla | Indicador y orden observado cuando están disponibles | Los fijados y el ranking alteran el orden; posición no es fecha |
| Contadores | Me gusta, comentarios, compartidos y guardados visibles, con hora de observación | Valores abreviados y cambiantes; no son contadores históricos |
| Cobertura | Estado por fuente, cantidad, límites y notas de extracción | No conocemos el total histórico ni lo eliminado u oculto |

Las capturas completas de página pueden mostrar elementos alrededor de la publicación y se conservan solo como referencia: no se envían automáticamente al OCR ni a visión. El texto se extrae de tarjetas recortadas o fotogramas. El extractor de descripción se limita al reproductor visible y evita incorporar recomendaciones como si fueran afirmaciones del titular. Revisa también el contexto de la imagen: una cita, un meme o un diálogo de película no son una declaración autobiográfica.

Los recortes verificados se muestran como evidencia; las páginas completas y los fotogramas antiguos sin comprobación quedan como referencias de diagnóstico. Si una superposición no puede cerrarse con un control conocido o no puede aislarse el medio, se registra el obstáculo y se omite su OCR. No se ocultan CAPTCHAs para simular una captura correcta.

El límite inicial es de 20 publicaciones por fuente y 8 rondas de desplazamiento; la API admite entre 1 y 200 registros por fuente y entre 1 y 50 rondas. Llegar a esos límites no acredita una captura completa.

## Fechas y análisis temporal

| Campo | Qué significa | Uso en el informe |
| --- | --- | --- |
| `published_at` | Fecha de publicación del contenido original | Cronología de originales, separada de las interacciones |
| `interaction_at` | Fecha verificable en que el perfil interactuó | Cronología de interacciones solo con `interaction_date_source` válido |
| `interaction_date_source` | `visible` o `user_verified` | Procedencia requerida para admitir una fecha de interacción |
| `observed_at` | Momento de esta captura/importación | Registro de observación |
| `first_seen_at` | Primera observación conservada por Huella para ese perfil, fuente y enlace | Seguimiento de futuras capturas; nunca fecha del repost |
| `order_index` | Posición observada en la muestra de la rejilla | Referencia de navegación, sin valor de fecha |

El colector solo interpreta fechas absolutas completas visibles con año, mes y día. Conserva el texto original de la fecha cuando no puede interpretarlo. No deduce años a partir de etiquetas como «9-12», ni extrae una fecha de interacción del identificador del video.

**Sin una fecha de interacción verificable no se puede reconstruir cuándo se hizo un repost o un Me gusta.** Un video publicado en septiembre pudo compartirse meses después. Las futuras capturas permiten saber cuándo Huella empezó a observarlo, pero `first_seen_at` tampoco demuestra cuándo ocurrió la interacción. El colector actual no obtiene automáticamente una fecha histórica de repost.

Para publicaciones del propio perfil se conserva la cronología de publicación original. La cronología de interacciones se mantiene aparte y no se completa artificialmente con esa fecha. Una fecha corregida por el usuario requiere evidencia: el formulario registra su procedencia como verificación manual.

Los temas y el tono explícito se calculan mediante reglas léxicas locales sobre descripción, OCR, transcripción y resumen visual. El informe vincula los resultados con sus registros. Deduplica dentro de cada perfil/fuente; el mismo video observado en dos fuentes se mantiene en ambas.

Las diferencias entre meses son descriptivas: requieren al menos dos registros en cada mes comparado, una diferencia de proporción de al menos 25 puntos y dos registros del tema en alguno de esos meses. No representan significancia estadística ni explican causas. Las etiquetas pueden solaparse, por lo que los porcentajes temáticos no tienen que sumar 100 %.

## Archivos, audio y visión local

La carga de archivos admite PNG/JPG/JPEG/WEBP, MP4/MOV/WEBM y WAV/MP3/M4A, hasta **100 MB por archivo**. Las imágenes tienen un límite de 40 megapíxeles. Adjunta solo el medio correspondiente a la evidencia seleccionada.

- **OCR:** incluido en `requirements.txt` mediante RapidOCR y OpenCV. Mantiene el original, aplica reescalado, CLAHE y recortes para leer texto, y reutiliza el modelo entre capturas. La primera lectura puede tardar más.
- **Video adjunto:** OpenCV muestrea hasta 8 puntos repartidos en el video, mantiene el orden y omite duplicados visuales. Los nombres guardan offsets relativos al video; si se estiman a partir de FPS, puede haber imprecisión en video de velocidad variable. No son fechas de publicación.
- **Transcripción:** integración opcional con faster-whisper. Procesa hasta los primeros 10 minutos de audio. Puede omitir canto, música o habla poco clara; no identifica canciones.
- **Visión:** integración opcional con un modelo visual local de Ollama para describir escenas, acciones, texto y contexto. Recibe hasta 8 imágenes y no debe inferir personalidad, atributos sensibles ni vida privada.

En el entorno preparado para este proyecto, OCR y OpenCV están instalados y se comprobaron con imágenes y un video sintéticos. **Whisper y Ollama no están configurados ni se descargaron sus modelos.** Las funciones ausentes devuelven notas explicativas; un campo vacío no demuestra que el contenido carezca de texto, habla o contexto.

### Variables de entorno

`.env.example` es una referencia. **El código no carga `.env` automáticamente.** Configura las variables en la misma PowerShell desde la que arranques el servidor; reinícialo después de cambiarlas.

Ejemplo para elegir otra carpeta local de datos:

```powershell
$env:HUELLA_DATA_DIR = 'D:\Huella\datos'
.\start.ps1
```

Para transcribir con un modelo faster-whisper **ya disponible localmente**, instala primero el paquete opcional y sustituye la ruta por la de tu modelo:

```powershell
uv pip install --python .\.venv\Scripts\python.exe faster-whisper
$env:WHISPER_MODEL = 'D:\Modelos\faster-whisper-local'
$env:WHISPER_ALLOW_DOWNLOAD = '0'
.\start.ps1
```

También puedes instalar el paquete con `python.exe -m pip` si tu entorno incluye pip. `WHISPER_ALLOW_DOWNLOAD=1` permite una descarga solicitada explícitamente; el valor predeterminado es `0`. El modelo se ejecuta en CPU con `int8`. Véase [faster-whisper](https://github.com/SYSTRAN/faster-whisper).

Para visión, instala y arranca Ollama por separado, prepara un modelo compatible con imágenes y establece su nombre exacto mediante `$env:OLLAMA_MODEL`. No hay descarga automática de modelos desde Huella.

| Variable | Predeterminado | Efecto |
| --- | --- | --- |
| `HUELLA_DATA_DIR` | `data/` dentro del proyecto | Carpeta de informes y evidencias |
| `HUELLA_PORT` | `8765` | Puerto local alternativo si otra instancia ocupa el predeterminado |
| `HUELLA_RELOAD` | `0` | `1` recarga código durante desarrollo; cancela trabajos al reiniciar |
| `WHISPER_MODEL` | Vacío | Ruta o nombre de modelo local/caché; vacío desactiva transcripción |
| `WHISPER_ALLOW_DOWNLOAD` | `0` | Solo `1` habilita descarga de modelo Whisper |
| `OLLAMA_MODEL` | Vacío | Nombre del modelo de visión local instalado |
| `OLLAMA_URL` | `http://127.0.0.1:11434` | Solo localhost/loopback, sin rutas ni credenciales |
| `OLLAMA_TIMEOUT_SECONDS` | `60` | Espera de la petición local, limitada a 5–120 segundos |
| `GEMINI_API_KEY` | Vacío | Credencial opcional de Google; selecciona Gemini en Ajustes IA |

La integración de Ollama desactiva proxies y redirecciones, comprueba metadatos antes de enviar imágenes y rechaza modelos cloud o alias remotos detectados. El contexto de páginas y capturas se trata como datos no confiables, no como instrucciones. Los resúmenes generados requieren revisión. Consulta la [API de generación de Ollama](https://docs.ollama.com/api/generate).

## Importar evidencia manual

`POST /api/import` crea un informe a partir de registros. Este ejemplo es **enteramente ficticio**, no contiene enlaces a perfiles ni publicaciones reales y deja la fecha de interacción desconocida:

```json
{
  "profile": "demo_ficticia",
  "collection": {
    "method": "manual_import",
    "note": "Ejemplo ficticio para probar el esquema. No representa una cuenta real."
  },
  "records": [
    {
      "source": "reposts",
      "url": "",
      "caption": "Escena de película sobre una despedida. #cine",
      "published_at": "2025-09-05",
      "interaction_at": null,
      "interaction_date_source": null,
      "observed_at": "2026-10-03T12:00:00-04:00",
      "first_seen_at": "2026-10-03T12:00:00-04:00",
      "hashtags": ["#cine"],
      "music": {},
      "notes": ["Todos los datos y fechas de este registro son ficticios."]
    }
  ]
}
```

Guárdalo como `ejemplo.json` y envíalo al servidor local:

```powershell
$body = Get-Content -LiteralPath .\ejemplo.json -Raw -Encoding UTF8
Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8765/api/import' -ContentType 'application/json; charset=utf-8' -Body ([System.Text.Encoding]::UTF8.GetBytes($body))
```

Las fuentes admitidas son `uploads`, `reposts`, `likes` y `manual`; el máximo es de 2.000 registros por importación. Una URL real debe ser HTTPS y apuntar a una publicación `/video/` o `/photo/` de TikTok. Los campos `screenshots` y `frames` importados se descartan: adjunta los medios por la interfaz o el endpoint de carga para mantener las rutas dentro de los artefactos locales.

## API y arquitectura

La documentación interactiva de FastAPI está en [la API local](http://127.0.0.1:8765/docs).

| Método y ruta | Operación |
| --- | --- |
| `GET /api/state` | Estado de Chrome, dependencias configuradas, informes y trabajos |
| `POST /api/session/open` | Abrir Chrome dedicado; cuerpo `{"profile":""}` para iniciar sesión |
| `POST /api/session/close` | Cerrar Chrome conservando el perfil local |
| `POST /api/capture` | Capturar `profile`, `sources`, `max_items`, `scroll_rounds`, `mode`, `headless`; devuelve un trabajo |
| `GET /api/profiles` / `POST /api/profiles` | Listar o guardar perfiles de búsqueda |
| `GET /api/profiles/{profile}/report` | Informe acumulado del perfil |
| `POST /api/profiles/{profile}/avatar` | Guardar foto local normalizada |
| `GET /api/ai/settings` / `PUT /api/ai/settings` | Configurar visión; nunca devuelve la API key |
| `DELETE /api/ai/key` | Borrar la clave configurada desde el panel |
| `GET /api/jobs/{job_id}` | Consultar progreso y resultado del trabajo |
| `POST /api/jobs/{job_id}/cancel` | Solicitar cancelación de captura tras la evidencia actual |
| `GET /api/runs` / `GET /api/runs/{run_id}` | Listar o leer informes persistidos |
| `POST /api/import` | Importar JSON manual |
| `PATCH /api/runs/{run_id}/records/{record_id}` | Corregir texto, contexto o fechas verificadas |
| `POST /api/runs/{run_id}/records/{record_id}/media` | Adjuntar `file` en formulario multipart y procesarlo |
| `POST /api/runs/{run_id}/records/{record_id}/vision` | Interpretar imágenes con el proveedor configurado |
| `POST /api/runs/{run_id}/analyze` | Recalcular el informe |
| `GET /api/runs/{run_id}/export?format=json` | Exportar JSON; `format=md` exporta Markdown |
| `POST /api/demo` | Crear demostración señalada como ficticia |
| `GET /data/{path}` | Leer medios admitidos dentro de `data/artifacts/` |

`capabilities` indica que una dependencia/configuración existe; no prueba que un servicio o modelo opcional esté listo. Revisa las notas de procesamiento para conocer el resultado real.

| Archivo | Responsabilidad |
| --- | --- |
| `app/main.py` | API, validación de entradas, trabajos y servicio local |
| `app/capture.py` | Navegación visible mediante Playwright; un trabajador de Chrome |
| `app/media.py` | OCR, muestreo de archivos, transcripción y descripción visual opcionales |
| `app/vision.py` | Ajustes IA, clave cifrada, Gemini multimodal, caché y uso |
| `app/profiles.py` / `app/incremental.py` | Biblioteca acumulada y planificación incremental |
| `app/insights.py` / `app/availability.py` | Resumen, menciones, períodos y limitaciones observadas |
| `app/analysis.py` | Temas, fechas, deduplicación, cobertura y exportación Markdown |
| `app/store.py` | Persistencia JSON, rutas de artefactos y primera observación |
| `app/static/` | Interfaz web sin framework |
| `tests/` | Pruebas de backend, análisis y medios |

Los trabajos activos viven en memoria y no se reanudan tras reiniciar el servidor. Las evidencias guardadas permanecen en disco. La cancelación de captura es cooperativa y se aplica entre evidencias; no interrumpe una inferencia OCR/Whisper/Ollama en curso. Los trabajos independientes de procesamiento multimedia terminan su operación aunque se haya solicitado cancelación.

## Datos locales y sesión

### Publicación en Vercel

El destino solicitado para alojamiento es Vercel, no ChatGPT Sites. El proyecto actual funciona como una aplicación local: usa Chrome instalado, una sesión persistente, trabajos en segundo plano y archivos duraderos. No debe desplegarse sin adaptar esas dependencias a [los límites de Vercel Functions](https://vercel.com/docs/functions/limitations).

Para una versión remota se necesita separar el panel de un trabajador con almacenamiento persistente y acceso autenticado. La conexión a la sesión del equipo requiere diseñar ese enlace; publicar solo los archivos del frontend dejaría inoperantes las capturas y la biblioteca. `.vercelignore` excluye sesiones, datos, entornos y logs para evitar incluirlos en un despliegue futuro. Todavía no se ha publicado esta app.

- `.browser-profile/` contiene cookies y datos de sesión de Chrome. Es información privada: no compartir, subir a Git ni servir como contenido estático. Ya está excluida en `.gitignore`.
- `data/runs/` conserva informes JSON; `data/artifacts/` conserva capturas y medios. Ambos pueden contener datos personales o material sensible del contenido consultado. `data/` está excluido de Git.
- `.env`, `.venv/`, cachés y logs también están excluidos de Git. No se deben incluir credenciales en JSON de importación o capturas.
- El servidor se publica solo en loopback y limita Host, Origin y rutas de evidencia. No tiene autenticación multiusuario ni cifrado propio de archivos; está pensado para un equipo local de confianza. No lo expongas directamente a la red.
- Las solicitudes a TikTok ocurren en el navegador. OCR, reglas de análisis y Whisper se ejecutan localmente; Ollama se acepta únicamente en loopback. Gemini es opcional y envía las imágenes y textos seleccionados a Google. La instalación inicial y una descarga de modelo habilitada explícitamente necesitan red.
- Para respaldar un informe conserva su JSON y sus artefactos asociados. La exportación JSON/Markdown por sí sola no empaqueta los archivos multimedia ni la sesión de Chrome.

## Verificación y límites pendientes

Ejecuta las pruebas desde la raíz:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Hay pruebas de normalización y rutas, importaciones, fechas, deduplicación, exportación, límites de medios, falta de dependencias, restricciones de Ollama, OCR real con imagen generada y muestreo de MP4 sintético. Los casos que necesitan dependencias opcionales se omiten cuando faltan. Las pruebas no descargan modelos Whisper/Ollama ni consultan perfiles reales.

La suite cubre análisis, separación de cronologías, importación/exportación, validación de rutas y fechas, OCR real y muestreo de video.

El OCR y el muestreo local se comprobaron con material sintético. Esto **no valida una extracción completa de un perfil real**: la captura en TikTok depende del inicio de sesión manual, la visibilidad de cada pestaña, el contenido cargado y los selectores de su interfaz, que pueden cambiar. Para evaluar una muestra real hay que inspeccionar sus capturas, errores y estados de cobertura.

Para ampliar el contexto, prioriza revisar carruseles completos, adjuntar el audio/video correspondiente, completar fechas solo cuando exista evidencia y contrastar cada tema con la publicación original. Las mejoras en reconocimiento o cobertura no convierten las publicaciones en pruebas de personalidad o vida privada.
