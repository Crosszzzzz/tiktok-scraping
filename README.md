# Huella · Observatorio local de contenido

Aplicación local para reunir evidencias visibles de TikTok y comparar sus temas a lo largo del tiempo. Trabaja con publicaciones del perfil, republicaciones y Me gusta públicos; conserva capturas, textos, metadatos y las limitaciones de cada muestra.

El informe describe **contenido observado**. Una frase de despedida, una canción o un repost no prueban una ruptura, un estado emocional ni una vivencia del titular. El proyecto no construye diagnósticos, perfiles psicológicos ni inferencias de atributos privados.

## Inicio en WSL2

Ejecuta la aplicación en WSL2 con Python 3.12. Instala Chrome en **Windows**, no en Linux. Desde la raíz del proyecto, con el entorno local ya preparado:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
sh start.sh
```

`start.sh` usa la raíz del proyecto, no el directorio desde el que lo invocas.
No instala paquetes automáticamente y el servidor permanece en loopback.

### Configuración local sin guardar la clave en la interfaz

El inicio mediante `python -m app` carga el archivo `.env` de la raíz antes
de importar los consumidores de configuración. Una variable ya presente en el
proceso tiene prioridad. Importar módulos en pruebas no carga ese archivo.
Puedes crear `.env` localmente con `GEMINI_API_KEY`, `GEMINI_MODEL=gemini-3.5-flash-lite` y `HUELLA_PORT` (8765 por
defecto); nunca compartas ese archivo ni su contenido. Se aceptan asignaciones
simples, valores entre comillas y comentarios; no se expanden variables ni se
ejecutan comandos. Los lanzadores usan `python -m app`. La alternativa
`uvicorn app.main:app` no lee `.env`: exporta previamente las variables necesarias
en el proceso y configura su puerto mediante `--port`.
La clave del entorno no se elimina desde el panel y en Linux puede permanecer
exclusivamente en ese entorno. Esto no valida disponibilidad, cuota ni precio.

### Chrome de Windows, una sola sesión dedicada

Abre la URL indicada por el servidor, normalmente `http://127.0.0.1:8765`. `Ctrl+C` detiene la aplicación. No hay aplicación ni lanzador independiente para Windows/macOS/Linux nativo; el archivo PowerShell del puente sirve exclusivamente a WSL.

El puente descubre Chrome en los directorios de instalación de Windows mediante `powershell.exe`. Crea el perfil dedicado `%LOCALAPPDATA%\HuellaBrowser\`; no usa tu perfil habitual ni copia cookies. Puedes intentar contenido público sin iniciar sesión. Si TikTok exige acceso, lo completas una vez en esa ventana; Chrome conserva la sesión para futuras ejecuciones mientras siga siendo válida.

Chrome abre CDP en loopback de Windows con puerto asignado por el sistema. El puente verifica PID, inicio del proceso y la identidad websocket contra metadatos propios del perfil antes de reutilizarlo. Un bloqueo de propiedad detiene la conexión: nunca se adjunta un puerto ajeno. El websocket viaja por stdin/stdout de PowerShell a un socket WSL `127.0.0.1` de una sola conexión. El diseño no expone CDP a LAN/NAT ni modifica firewall o políticas; la consulta de propiedad en Windows desactiva proxies del entorno.

Windows puede rechazar la ejecución directa de un `.ps1` desde la ruta UNC de WSL con un error de AuthorizationManager. La aplicación copia únicamente su helper a `%LOCALAPPDATA%\HuellaBrowser\huella-cdp-bridge.ps1` y lo ejecuta desde ese directorio local autorizado, sin cambiar la política de ejecución. Si Windows también bloquea el helper local, se informa el bloqueo y no se intenta evadirlo.

Playwright usa `connect_over_cdp` y `browser.contexts[0]`, el contexto persistente existente, no un contexto incógnito nuevo. Al detener el servidor se desconecta el transporte; no se cierra el navegador ni otras pestañas. **No cierres Chrome antes de capturar.** CDP tiene menor fidelidad que un navegador lanzado directamente por Playwright; la validación real sigue siendo necesaria.

PowerShell se busca primero en PATH y después únicamente en su directorio estándar de los discos Windows montados, respetando mayúsculas como `WINDOWS`. `wslpath` se descubre por separado, con respaldo en `/usr/bin/wslpath`. Si falta una herramienta o WSL deniega ejecutar Windows, la interfaz conserva el motivo del HTTP 409; no simula una conexión ni abre Chrome de Linux. No se instalan paquetes ni cambian políticas del equipo automáticamente. `requirements.lock.txt` conserva versiones; úsalo en lugar de `requirements.txt` si necesitas reproducir el entorno.

Después de actualizar, detén el servidor con `Ctrl+C` y reinicia desde la raíz con `sh start.sh`; recarga el panel. No hace falta modificar PATH permanentemente. Para una instalación no estándar, puedes indicar una ruta WSL absoluta existente a `powershell.exe` solo para ese inicio: `HUELLA_POWERSHELL_EXE=/mnt/c/WINDOWS/System32/WindowsPowerShell/v1.0/powershell.exe sh start.sh`. Una ruta inválida se rechaza sin respaldo silencioso. Si el ejecutable existe pero WSL no puede ejecutarlo, reiniciar la aplicación no repara la interoperabilidad: solicita al administrador revisar la integración WSL/Windows, sin cambiar políticas desde Huella.

## Primera captura

1. Inicia el servidor y abre el panel local.
2. Introduce la URL, `@usuario` o el nombre del perfil.
3. Selecciona alcance y profundidad; pulsa **Analizar**. Chrome se conecta automáticamente y obtiene las respuestas de datos de cada fuente y sus portadas, con los límites elegidos. Si esa respuesta no está disponible, intenta la captura visual existente. No necesitas guardar perfiles ni ejecutar una vista previa aparte.
4. Revisa progreso y después el informe ejecutivo; exporta Markdown o JSON. Evidencias, historial, fechas y ajustes de API están plegados. El historial no se abre automáticamente; los datos ficticios no se seleccionan como informes reales.
5. Si se detiene por acceso, CAPTCHA o introducción, usa **Abrir navegador / resolver acceso**. Resuélvelo tú en HuellaBrowser y pulsa **Analizar** otra vez, sin cerrar Chrome. No hay espera automática prolongada ni resolución del desafío.

La navegación siempre usa la misma sesión dedicada y visible, autenticada o no. Una sesión anónima aquí significa no haber iniciado sesión, no crear otra ventana/incógnito en cada trabajo. No hay demos, importación ni publicación pública en el flujo soportado.

Una verificación, un límite de acceso o un perfil realmente restringido detienen la captura para revisión. Un aviso de cuenta privada no bloquea tarjetas que ya sean visibles y accesibles; no se intenta obtener lo oculto. Una pestaña ausente, privada o que no carga no demuestra ausencia de actividad.

| Profundidad | Trabajos por fuente | Desplazamientos por fuente |
| --- | ---: | ---: |
| Rápida | 5 | 3 |
| Personalizada | 1–200 | 1–50 |
| Profunda | 60 | 25 |
| Todo accesible | 200 | 50 |

«Todo accesible» sigue siendo acotado. Guarda lo observado y permite continuar con otra captura incremental; no garantiza recorrer todo el historial. Se conservan límites y preferencias por perfil. No se reanuda un trabajo activo después de reiniciar el servidor.

**Últimos 30 días** usa la fecha original visible o verificada de publicaciones propias, hasta hoy. La API rechaza ese filtro para compartidos y Me gusta, porque no conoce la fecha de acción. Las observaciones con fecha desconocida se conservan con `date_filter_status=unknown_date`, pero no pasan al OCR, Gemini ni al análisis filtrado; tampoco se adivina su fecha. El archivo acumulado puede contener capturas anteriores: revisa el informe individual para el alcance exacto de la ejecución.


## Recolección estructurada y portadas

El flujo Analizar intenta primero leer las respuestas que TikTok entrega a la sesión
HuellaBrowser para publicaciones, reposts y Me gusta. La arquitectura toma como
referencia [Repostify](https://github.com/xtofuub/Repostify), con una implementación
Python propia en `app/tiktok_feed.py`; no requiere instalar Repostify, otra API ni
entregar cookies a terceros. Se reutiliza Playwright, que ya es una dependencia.

Cada fuente se abre desde el perfil y se valida su `secUid`, endpoint y continuidad
de cursores. TikTok realiza su propia paginación al desplazarse; no se inventan
firmas ni se reutilizan peticiones de otros perfiles. Los límites de desplazamientos
y trabajos siguen vigentes. Si faltan la identidad o las respuestas iniciales, se
usa el colector visual como respaldo. Un CAPTCHA sigue requiriendo atención manual.

Cada registro conserva enlace, autor original, descripción, fecha de publicación,
métricas y portada disponible. Las portadas se descargan desde dominios CDN admitidos,
con límites de tamaño, sin reenviar cookies ni seguir redirecciones. Se decodifican y
guardan localmente como JPEG; OCR y Gemini pueden usarlas. Una portada no equivale a
una captura completa del video: el flujo estructurado no obtiene todas sus escenas,
audio ni todas las diapositivas. Los videos/archivos adjuntos mantienen su procesamiento
existente. Las capturas de detalle siguen disponibles en el respaldo visual.

La interfaz identifica las fuentes obtenidas mediante datos estructurados y las que
usaron el respaldo visual.

El informe registra `method=tiktok_web_response` por fuente, páginas observadas y
motivo de parada. Si falla una portada se conserva el enlace y se marca procesamiento
parcial para reintentar. Una respuesta bloqueada o inválida nunca significa perfil
vacío. `createTime` no se convierte en fecha de repost o Me gusta.

## Perfiles guardados y actualizaciones

Cada perfil conserva nombre, notas, foto, fuentes y límites de captura, además de un archivo acumulado de evidencia. La foto se intenta obtener del perfil público; si falla, se muestran iniciales y puedes subir una imagen. No se genera un rostro ficticio.

El modo **incremental** vuelve a leer las rejillas y procesa los elementos nuevos o pendientes; reutiliza OCR, imágenes y contexto ya guardados. No se detiene al primer elemento conocido porque los fijados y el orden pueden cambiar. El límite de elementos se aplica al trabajo nuevo, no a las filas reutilizadas. El modo **actualizar todo** fuerza una nueva extracción dentro de los límites elegidos.

Se conservan la primera y última observación, las correcciones manuales y la procedencia del informe original. La ausencia en una muestra nueva no borra registros anteriores ni demuestra que hayan sido eliminados. Los intentos parciales tienen una espera de seis horas antes del reintento automático; actualizar todo permite reintentar antes. Los fotogramas de la versión anterior, que no comprobaba superposiciones, se reparan una vez al volver a encontrarlos.

El archivo acumulado reúne contenido de todas las capturas. Los informes individuales permiten revisar cada ejecución y sus bloqueos. La fecha de primera observación sigue sin ser una fecha de repost. Las menciones conservan su texto, fuente y autoría cuando se puede verificar; una etiqueta no establece una relación entre personas.

La captura y el acceso manual usan la misma ventana visible. No hay cambio a headless. El diagnóstico conserva qué pestañas se detectaron y qué impidió continuar. La ayuda de navegación «Entendido» se cierra antes de capturar; un CAPTCHA detiene el trabajo sin guardar imágenes del desafío.

## Interpretar imágenes con Gemini

En los ajustes plegados de API, selecciona Gemini y autoriza el envío remoto. Configura `GEMINI_API_KEY` mediante el inicio seguro o introduce la clave opcional en el campo de contraseña. En WSL una clave introducida en el panel permanece en memoria del servidor, no se cifra con DPAPI ni se guarda en texto plano. La clave no se devuelve a la interfaz ni se incluye en informes.

La interpretación durante Analizar usa recortes verificados junto con descripción, OCR, etiqueta musical y transcripción disponible. Tiene un límite por ejecución y está desactivada inicialmente. **Gemini envía esas imágenes y textos a Google**, no la sesión ni las cookies. La alternativa soportada es **Solo OCR local**. Ollama ya no está soportado; quedan helpers internos heredados sin ruta en este flujo.

Modelo predeterminado: `gemini-3.5-flash-lite`, orientado a coste reducido. Puedes elegir otro modelo, por ejemplo `gemini-3.5-flash`, `gemini-2.5-flash-lite` o `gemini-3.8-flash`. `GEMINI_MODEL` tiene prioridad sobre el selector mientras esté configurado en el entorno. Solo se hace un intento con el modelo elegido: no hay sustitución automática por uno más caro ni recorrido de modelos al agotar cuota. Disponibilidad, nivel gratuito y compatibilidad con créditos dependen de tu cuenta/proyecto; no se garantizan.

Gemini puede interpretar escenas y prendas aunque no contengan texto. El resultado separa observaciones, posibles referencias culturales, texto visible e incertidumbres. Una prenda puede guardar relación con un estilo regional; eso no acredita la etnia, procedencia o identidad de quien la lleva. La etiqueta de una canción aporta contexto, pero no equivale a haber escuchado su audio.

Se redimensionan y deduplican hasta cuatro imágenes por solicitud. Una caché por contenido, contexto y modelo evita repetir peticiones idénticas. Se registran solicitudes y tokens informados por el proveedor; no se inventa un coste en dólares. La disponibilidad y cuota dependen de tu cuenta. Consulta [visión con Gemini](https://ai.google.dev/gemini-api/docs/image-understanding) y [precios y uso de datos](https://ai.google.dev/gemini-api/docs/pricing); el nivel gratuito puede utilizar el contenido para mejorar productos.

Sin Gemini activado y una clave, el OCR sigue funcionando. Los errores de cuota o autenticación detienen los intentos visuales automáticos y conservan la evidencia original.

## Qué recoge y qué puede faltar

| Evidencia | Implementación actual | Límite que hay que revisar |
| --- | --- | --- |
| Publicaciones, compartidos y Me gusta | Respuestas de datos de TikTok y portadas; respaldo con rejillas visibles | Muestra acotada; una pestaña puede no estar disponible |
| Texto de publicación | Descripción visible o texto alternativo de miniatura | Puede truncarse o faltar según la página |
| Contexto visual del video | Hasta 7 capturas renderizadas, incluido inicio y final, con offsets y deduplicación exacta | No exporta video completo; escenas o texto fugaz pueden faltar |
| Imágenes y carruseles | Imagen visible o tarjeta recortada cuando puede aislarse sin obstáculos | No garantiza todas las diapositivas. Puedes adjuntar imágenes adicionales |
| Texto sobreimpreso | OCR local del original y variantes con contraste/recorte | Errores con tipografías, movimiento, idiomas, ironía o bajo contraste |
| Música | Etiqueta/enlace musical visible, con procedencia | No es reconocimiento de audio; no acredita la letra ni el significado personal |
| Hashtags y autor del contenido | Campos visibles guardados por registro | El autor del video y el perfil que lo republica pueden ser distintos |
| Fijados y posición de rejilla | Indicador y orden observado cuando están disponibles | Los fijados y el ranking alteran el orden; posición no es fecha |
| Contadores | Me gusta, comentarios, compartidos y guardados visibles, con hora de observación | Valores abreviados y cambiantes; no son contadores históricos |
| Cobertura | Estado por fuente, cantidad, límites y notas de extracción | No conocemos el total histórico ni lo eliminado u oculto |

Toda captura, incluso de rejilla y diagnóstico, comprueba obstáculos después de preparar la página y justo antes de guardar. Solo se cierran controles conocidos, como «Entendido» o rechazo de cookies; jamás se oculta una superposición mediante CSS. Un CAPTCHA requiere resolución manual y no se captura; una introducción persistente como «avanza con el mouse para continuar» detiene el intento antes de cualquier imagen. Los diagnósticos nuevos no van a OCR ni a Gemini. Los recortes anteriores sin la nueva marca de seguridad no se reutilizan para visión y requieren recaptura.

Los recortes verificados se muestran como evidencia; las páginas completas y los fotogramas antiguos sin comprobación quedan como referencias de diagnóstico. Si una superposición no puede cerrarse con un control conocido o no puede aislarse el medio, se registra el obstáculo y se omite su OCR. No se ocultan CAPTCHAs para simular una captura correcta.

La profundidad rápida es el valor inicial; los límites personalizados admiten 1–200 trabajos y 1–50 rondas por fuente. Las filas conocidas reutilizadas no consumen presupuesto de procesamiento. Alcanzar límites no acredita una captura completa.

## Fechas y análisis temporal

| Campo | Qué significa | Uso en el informe |
| --- | --- | --- |
| `published_at` | Fecha de publicación del contenido original | Cronología de originales, separada de las interacciones |
| `interaction_at` | Fecha verificable en que el perfil interactuó | Cronología de interacciones solo con `interaction_date_source` válido |
| `interaction_date_source` | `visible` o `user_verified` | Procedencia requerida para admitir una fecha de interacción |
| `observed_at` | Momento de esta captura/importación | Registro de observación |
| `first_seen_at` | Primera observación conservada por Huella para ese perfil, fuente y enlace | Seguimiento de futuras capturas; nunca fecha del repost |
| `order_index` | Posición observada en la muestra de la rejilla | Referencia de navegación, sin valor de fecha |

El colector acepta `createTime` de la respuesta de TikTok como fecha de publicación original (`published_date_source=tiktok_web_response`). En el respaldo visual solo interpreta fechas absolutas completas visibles con año, mes y día. Conserva el texto original de la fecha cuando no puede interpretarlo. No deduce años a partir de etiquetas como «9-12», ni extrae una fecha de interacción del identificador del video.

**Sin una fecha de interacción verificable no se puede reconstruir cuándo se hizo un repost o un Me gusta.** Un video publicado en septiembre pudo compartirse meses después. Las futuras capturas permiten saber cuándo Huella empezó a observarlo, pero `first_seen_at` tampoco demuestra cuándo ocurrió la interacción. El colector actual no obtiene automáticamente una fecha histórica de repost.

Para publicaciones del propio perfil se conserva la cronología de publicación original. La cronología de interacciones se mantiene aparte y no se completa artificialmente con esa fecha. Una fecha corregida por el usuario requiere evidencia: el formulario registra su procedencia como verificación manual.

Los temas y el tono explícito se calculan mediante reglas léxicas locales sobre descripción, OCR, transcripción y resumen visual. El informe vincula los resultados con sus registros. Deduplica dentro de cada perfil/fuente; el mismo video observado en dos fuentes se mantiene en ambas.

Las diferencias entre meses son descriptivas: requieren al menos dos registros en cada mes comparado, una diferencia de proporción de al menos 25 puntos y dos registros del tema en alguno de esos meses. No representan significancia estadística ni explican causas. Las etiquetas pueden solaparse, por lo que los porcentajes temáticos no tienen que sumar 100 %.

## Archivos, OCR y audio opcional

La carga de archivos admite PNG/JPG/JPEG/WEBP, MP4/MOV/WEBM y WAV/MP3/M4A, hasta **100 MB por archivo**. Las imágenes tienen un límite de 40 megapíxeles. Adjunta solo el medio correspondiente a la evidencia seleccionada.

- **OCR:** incluido en `requirements.txt` mediante RapidOCR y OpenCV. Mantiene el original, aplica reescalado, CLAHE y recortes para leer texto, y reutiliza el modelo entre capturas. La primera lectura puede tardar más.
- **Video adjunto:** OpenCV muestrea hasta 8 puntos repartidos en el video, mantiene el orden y omite duplicados visuales. Los nombres guardan offsets relativos al video; si se estiman a partir de FPS, puede haber imprecisión en video de velocidad variable. No son fechas de publicación.
- **Transcripción:** integración opcional con faster-whisper. Procesa hasta los primeros 10 minutos de audio. Puede omitir canto, música o habla poco clara; no identifica canciones.
- **Visión:** solo Gemini opcional; como máximo cuatro imágenes reducidas por solicitud. Ningún proveedor debe inferir atributos sensibles o vida privada.

Whisper es opcional y se carga únicamente al procesar audio o video adjunto por el usuario. Las capturas del navegador no contienen audio ni producen transcripción. No se descargaron modelos durante esta implementación; funciones ausentes producen notas, no contenido inventado.

### Variables de entorno

El inicio carga `.env` de la raíz sin sobrescribir variables del proceso y sin imprimir claves. Reinicia tras cambiarlo. `HUELLA_DATA_DIR` relativo se resuelve desde la raíz, no desde el directorio de invocación. Elige una carpeta privada del equipo; no compartas sesiones ni credenciales.

Ejemplo para elegir otra carpeta local de datos:

```sh
HUELLA_DATA_DIR=data sh start.sh
```

Para transcribir con un modelo faster-whisper **ya disponible localmente**, instala primero el paquete opcional y sustituye la ruta por la de tu modelo:

```sh
.venv/bin/python -m pip install faster-whisper
WHISPER_MODEL=/ruta/al/modelo/local WHISPER_ALLOW_DOWNLOAD=0 sh start.sh
```

`WHISPER_ALLOW_DOWNLOAD=1` permite una descarga solicitada explícitamente; el valor predeterminado es `0`. El modelo se ejecuta en CPU con `int8`. Véase [faster-whisper](https://github.com/SYSTRAN/faster-whisper).

También puedes configurar esas variables en `.env`. No hace falta Whisper para capturar imágenes u obtener OCR.

| Variable | Predeterminado | Efecto |
| --- | --- | --- |
| `HUELLA_DATA_DIR` | `data/` dentro del proyecto | Carpeta de informes y evidencias |
| `HUELLA_PORT` | `8765` | Puerto local alternativo si otra instancia ocupa el predeterminado |
| `HUELLA_RELOAD` | `0` | `1` recarga código durante desarrollo; cancela trabajos al reiniciar |
| `WHISPER_MODEL` | Vacío | Ruta o nombre de modelo local/caché; vacío desactiva transcripción |
| `WHISPER_ALLOW_DOWNLOAD` | `0` | Solo `1` habilita descarga de modelo Whisper |
| `GEMINI_API_KEY` | Vacío | Credencial opcional de Google; selecciona Gemini en Ajustes IA |
| `GEMINI_MODEL` | `gemini-3.5-flash-lite` | Modelo elegido, sin fallback automático |
| Chrome | Descubrimiento de Windows | Perfil dedicado HuellaBrowser; sin fallback Linux |

Los textos de páginas, OCR y capturas son datos no confiables, nunca instrucciones para el modelo. Revisa cada interpretación contra su evidencia.

## API y arquitectura

La documentación interactiva de FastAPI está en [la API local](http://127.0.0.1:8765/docs).

| Método y ruta | Operación |
| --- | --- |
| `GET /api/state` | Estado de Chrome, dependencias configuradas, informes y trabajos |
| `POST /api/session/open` | Conectar Chrome dedicado de Windows; contexto persistente |
| `POST /api/session/login` | Mostrar la misma ventana para acceso manual; no cerrarla |
| `POST /api/preflight` | Inspección explícita de rejillas, sin capturas ni IA |
| `POST /api/session/close` | Desconectar el puente, sin cerrar Chrome |
| `POST /api/auto` | Inspección visible y captura/análisis local en un paso |
| `POST /api/capture` | Capturar `profile`, `sources`, `max_items`, `scroll_rounds`, `mode`, `headless`; devuelve un trabajo |
| `GET /api/profiles` / `POST /api/profiles` | Listar o guardar perfiles de búsqueda |
| `GET /api/profiles/{profile}/report` | Informe acumulado del perfil |
| `POST /api/profiles/{profile}/avatar` | Guardar foto local normalizada |
| `GET /api/ai/settings` / `PUT /api/ai/settings` | Configurar visión; nunca devuelve la API key |
| `DELETE /api/ai/key` | Borrar la clave configurada desde el panel |
| `GET /api/jobs/{job_id}` | Consultar progreso y resultado del trabajo |
| `POST /api/jobs/{job_id}/cancel` | Solicitar cancelación de captura tras la evidencia actual |
| `GET /api/runs` / `GET /api/runs/{run_id}` | Listar o leer informes persistidos |
| `PATCH /api/runs/{run_id}/records/{record_id}` | Corregir texto, contexto o fechas verificadas |
| `POST /api/runs/{run_id}/records/{record_id}/media` | Adjuntar `file` en formulario multipart y procesarlo |
| `POST /api/runs/{run_id}/records/{record_id}/vision` | Interpretar imágenes con el proveedor configurado |
| `POST /api/runs/{run_id}/analyze` | Recalcular el informe |
| `GET /api/runs/{run_id}/export?format=json` | Exportar JSON; `format=md` exporta Markdown |
| `GET /data/{path}` | Leer medios admitidos dentro de `data/artifacts/` |

`capabilities` indica que una dependencia/configuración existe; no prueba que un servicio o modelo opcional esté listo. Revisa las notas de procesamiento para conocer el resultado real.

| Archivo | Responsabilidad |
| --- | --- |
| `app/main.py` | API, validación de entradas, trabajos y servicio local |
| `app/capture.py` | Navegación visible mediante Playwright; un trabajador de Chrome |
| `app/windows_browser.py` / `app/windows_cdp_bridge.ps1` | Puente stdio y verificación de Chrome dedicado, sin exposición de red |
| `app/media.py` | OCR, muestreo de archivos, transcripción y descripción visual opcionales |
| `app/vision.py` | Ajustes IA, clave cifrada, Gemini multimodal, caché y uso |
| `app/profiles.py` / `app/incremental.py` | Biblioteca acumulada y planificación incremental |
| `app/insights.py` / `app/availability.py` | Resumen, menciones, períodos y limitaciones observadas |
| `app/analysis.py` | Temas, fechas, deduplicación, cobertura y exportación Markdown |
| `app/store.py` | Persistencia JSON, rutas de artefactos y primera observación |
| `app/static/` | Interfaz web sin framework |
| `tests/` | Pruebas de backend, análisis y medios |

Los trabajos activos viven en memoria y no se reanudan tras reiniciar el servidor. Las evidencias guardadas permanecen en disco. La cancelación es cooperativa entre evidencias; no interrumpe una operación OCR/Whisper/Gemini en curso.

## Datos locales y sesión

La aplicación soportada es local; no expone rutas de demo, importación ni despliegue público. Los helpers históricos quedan aislados y los datos existentes no se borran.

- `%LOCALAPPDATA%\HuellaBrowser\` contiene cookies y datos de sesión privados en Windows. No compartir ni exportar. El antiguo `.browser-profile/`, si existe, queda intacto pero no se utiliza.
- `data/runs/` conserva informes JSON; `data/artifacts/` conserva capturas y medios. Ambos pueden contener datos personales o material sensible del contenido consultado. `data/` está excluido de Git.
- `.env`, `.venv/`, cachés y logs también están excluidos de Git. No se deben incluir credenciales en JSON de importación o capturas.
- El servidor se publica solo en loopback y limita Host, Origin y rutas de evidencia. No tiene autenticación multiusuario ni cifrado propio de archivos; está pensado para un equipo local de confianza. No lo expongas directamente a la red.
- Las solicitudes a TikTok ocurren en el navegador. OCR, análisis léxico y Whisper son locales. Gemini es opcional y envía imágenes y textos seleccionados a Google. Solo instalaciones y descargas solicitadas explícitamente necesitan red fuera del flujo de captura.
- Para respaldar un informe conserva su JSON y sus artefactos asociados. La exportación JSON/Markdown por sí sola no empaqueta los archivos multimedia ni la sesión de Chrome.

## Validación manual pendiente

Esta iteración se implementó sin añadir ni ejecutar pruebas automatizadas, por petición del usuario. Las comprobaciones estructurales no acreditan funcionamiento real. Para validar manualmente:

1. Ejecuta `sh start.sh` desde WSL2, abre el panel local y mantén IA desactivada.
2. Introduce una URL autorizada, selecciona Publicaciones y Rápida, y pulsa Analizar. Comprueba la inspección automática, los mínimos observados y cada recorte.
3. Si aparece un CAPTCHA o introducción, verifica que se detiene antes de imágenes/OCR/Gemini del desafío. Resuélvelo manualmente en HuellaBrowser, sin cerrar Chrome, y vuelve a analizar. No lo omitas.
4. Prueba Últimos 30 días solo con publicaciones. Comprueba fechas originales, observaciones sin fecha y exclusiones. Intentar el filtro con compartidos/Me gusta debe rechazarse. Comprueba límites de Personalizada y Todo accesible y sus razones de parada.
5. Exporta JSON y Markdown: revisa `analysis.executive`, cobertura por fuente, enlaces/IDs, fechas, limitaciones y próximos pasos. Los temas de pérdida o ruptura describen texto, no vivencias del titular.
6. Solo si aceptas envío y posible coste, configura Gemini y analiza una evidencia. Confirma modelo elegido y error de cuota sin cambio automático. Para audio, adjunta un archivo local con Whisper ya instalado/configurado.

El OCR y el muestreo local se comprobaron con material sintético. Esto **no valida una extracción completa de un perfil real**: la captura en TikTok depende del inicio de sesión manual, la visibilidad de cada pestaña, el contenido cargado y los selectores de su interfaz, que pueden cambiar. Para evaluar una muestra real hay que inspeccionar sus capturas, errores y estados de cobertura.

Para ampliar el contexto, prioriza revisar carruseles completos, adjuntar el audio/video correspondiente, completar fechas solo cuando exista evidencia y contrastar cada tema con la publicación original. Las mejoras en reconocimiento o cobertura no convierten las publicaciones en pruebas de personalidad o vida privada.
