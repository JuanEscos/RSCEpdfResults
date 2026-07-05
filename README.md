# RSCEpdfResults 🏆

Script automatizado en Python para descargar y procesar los PDF de resultados de Agility 2025 de la Real Sociedad Canina de España (RSCE) en formato JSON.

---

## 📋 Descripción

El script `rsce_agility_pdf_to_json.py` descarga de forma automática los resultados publicados en formato PDF desde la web oficial de la RSCE (`https://www.rsce.es`) y procesa sus tablas para generar datos limpios y estructurados en formato JSON.

Los resultados procesados se guardan localmente en:
`./data/agility/processed/resultados_{año}.json`

---

## 🛠️ Requisitos e Instalación

### Dependencias de Python
Este proyecto requiere las siguientes librerías principales (listadas en `requirements.txt`):
* `pdfplumber` (para extraer tablas de PDFs)
* `pandas` (para estructuración de datos)
* `requests` (para descarga de archivos)
* `numpy` (para manejo de valores vacíos y nulos)

### Paso a Paso para la Instalación

1. **Clonar el repositorio**:
   ```bash
   git clone https://github.com/JuanEscos/RSCEpdfResults.git
   cd RSCEpdfResults
   ```

2. **Crear y activar un entorno virtual**:
   * **En Linux/macOS**:
     ```bash
     python -m venv .venv
     source .venv/bin/activate
     ```
   * **En Windows**:
     ```bash
     python -m venv .venv
     .venv\Scripts\activate
     ```

3. **Instalar dependencias**:
   ```bash
   pip install -r requirements.txt
   ```

---

## 🚀 Ejecución

Para ejecutar el script de descarga y procesamiento de resultados:

```bash
python rsce_agility_pdf_to_json.py
```

---

## ⚙️ Automatización (GitHub Actions)

El repositorio incluye un flujo de integración continua (`.github/workflows/rsce_agility_results.yml`) que:
* Se ejecuta automáticamente **todos los días a las 04:10 UTC** (o manualmente).
* Descarga y procesa los PDF actualizados.
* Sube de forma segura los archivos JSON resultantes a un servidor remoto mediante SFTP.
* Verifica la integridad de la subida comparando hashes.
