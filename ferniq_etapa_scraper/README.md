# Ferniq Etapa Scraper

Scraper en Python con Playwright para extraer los datos visibles de la pestana `Etapa` del dashboard `FO - Etapa`, recorriendo un comercial cada vez y generando un Excel final. Tambien incluye un bot de Telegram para lanzar la extraccion con el texto `sacar datos` y recibir el resultado.

## Estructura

```text
ferniq_etapa_scraper/
|- .env.example
|- config_utils.py
|- guardar_sesion.py
|- extraer_etapa.py
|- telegram_bot.py
|- requirements.txt
`- README.md
```

## Requisitos

- Python 3
- Acceso valido a la web interna
- Un bot de Telegram creado con BotFather

## Instalacion

Desde la carpeta `ferniq_etapa_scraper`:

```bash
pip install -r requirements.txt
```

Instala el navegador de Playwright:

```bash
playwright install
```

## Configuracion opcional con .env

Puedes crear un archivo `.env` dentro de `ferniq_etapa_scraper` a partir de `.env.example`.

Ejemplo:

```env
FERNIQ_EMAIL=
FERNIQ_PASSWORD=
TELEGRAM_BOT_TOKEN=
TELEGRAM_ALLOWED_CHAT_ID=
```

El proyecto carga ese archivo automaticamente para `guardar_sesion.py` y `telegram_bot.py`.

## 1. Guardar sesion

`guardar_sesion.py` abre Chromium visible y guarda la sesion en `sesion_ferniq.json`.

Modo manual:

```bash
python guardar_sesion.py
```

Pasos:

1. Se abre `https://ferniq.fernfutures.com/app/opportunity-flow/stage`
2. Inicias sesion manualmente
3. Cuando veas el dashboard cargado, vuelves a la consola
4. Pulsas `ENTER`
5. Se guarda la sesion en `sesion_ferniq.json`

### Login automatico opcional

No pongas credenciales dentro del codigo. Si quieres automatizar el login, usa variables de entorno.

En PowerShell:

```powershell
$env:FERNIQ_EMAIL="tu_usuario"
$env:FERNIQ_PASSWORD="tu_password"
python guardar_sesion.py
```

El script intentara detectar los campos de usuario, password y envio. Si no puede completar el login, caera a modo manual.

## 2. Extraer datos de Etapa manualmente

```bash
python extraer_etapa.py
```

El script hace lo siguiente:

1. Abre la URL usando `sesion_ferniq.json`
2. Se asegura de estar en la pestana `Etapa`
3. Recorre la lista `COMERCIALES`
4. Abre el selector de `Comercial`
5. Intenta dejar seleccionado solo un comercial
6. Espera a que el dashboard se actualice
7. Intenta usar el boton `Descargar`
8. Si la descarga sirve, la procesa con `pandas`
9. Si la descarga no sirve, lee los datos visibles del DOM
10. Genera `resultado_etapa_ferniq.xlsx`

## 3. Usarlo desde Telegram

El bot ejecuta el scraper, lee `resultado_etapa_ferniq.xlsx` y responde con:

- un resumen por comercial
- precios por etapa para cada comercial
- el Excel final como documento adjunto

### Variables de entorno del bot

En PowerShell:

```powershell
$env:TELEGRAM_BOT_TOKEN="tu_token_del_bot"
$env:TELEGRAM_ALLOWED_CHAT_ID="tu_chat_id_opcional"
python telegram_bot.py
```

`TELEGRAM_ALLOWED_CHAT_ID` es opcional, pero recomendable para que solo responda en tu chat.

Si usas `.env`, basta con:

```bash
python telegram_bot.py
```

### Comandos y mensajes soportados

- `/start`
- `/sacar_datos`
- `sacar datos`

Cuando envias `sacar datos`, el bot:

1. Ejecuta la extraccion para todos los comerciales
2. Genera o regenera `resultado_etapa_ferniq.xlsx`
3. Lee el Excel
4. Te envia el resumen y el archivo

## Exportaciones intermedias

Si la descarga funciona, los archivos se guardan en:

```text
exports/
```

## Cambiar comerciales y mes

Edita estas variables al principio de `extraer_etapa.py`:

```python
COMERCIALES = [
    "Ismael Serrano",
    "Javier Barcelo",
    "Jesus Cabello",
    "Miguel Angel Haro",
    "Paco Buendia",
    "Agustin Parejo",
    "Ramon Jimenez",
]

MES = "Mayo 2026"
```

Nota: si el usuario autenticado es `Agustin Parejo` y en el selector aparece `Mi usuario`, el script ya contempla ese alias automaticamente.

## Si la sesion caduca

Si al ejecutar `extraer_etapa.py` o el bot de Telegram la web redirige al login o ya no carga el dashboard correctamente:

1. Borra o reemplaza `sesion_ferniq.json`
2. Ejecuta otra vez:

```bash
python guardar_sesion.py
```

3. Repite la extraccion o vuelve a usar el bot

## Notas

- El script registra errores por comercial y sigue con el siguiente
- Siempre intenta generar `resultado_etapa_ferniq.xlsx`, aunque falle alguno
- El bot ejecuta el scraper en modo `headless` para poder responder sin abrir ventana
- Los selectores del popup de `Comercial`, del login y del boton `Descargar` usan heuristicas razonables. Si la UI interna cambia, puede hacer falta ajustar esos selectores en `extraer_etapa.py` o `guardar_sesion.py`
