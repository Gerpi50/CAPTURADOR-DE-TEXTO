# Capturar Texto de Pantalla

Programa para Windows que copia el texto de **cualquier parte de la pantalla** (imágenes, videos, PDFs escaneados, ventanas que no dejan seleccionar) usando OCR.

1. Presioná **Ctrl+Shift+Z** (funciona desde cualquier programa) o el botón de la ventana.
2. La pantalla se congela: dibujá un rectángulo sobre el texto.
3. El texto aparece en la ventana y queda copiado en el portapapeles.

Para cancelar la selección: `ESC` o clic derecho.

## Instalación

1. Instalá [Python 3.8 o más nuevo](https://www.python.org/downloads/) (tildá "Add Python to PATH").
2. Instalá [Tesseract OCR](https://github.com/UB-Mannheim/tesseract/wiki). En el instalador, abrí *Additional language data* y tildá **Spanish**. Dejalo en la ruta por defecto.
3. Instalá las librerías:

   ```
   pip install -r requirements.txt
   ```
4. Ejecutá el programa:

   ```
   python CapturarTexto.py
   ```

## Cambiar el atajo de teclado

Ctrl+Shift+Z es también "rehacer" en muchos programas, y mientras esta app esté abierta esa combinación es solo de ella. Para usar otra, editá `ATAJOS_A_PROBAR` al principio de `CapturarTexto.py`. Si un atajo ya lo usa otro programa, se prueba el siguiente de la lista.

## Notas

- Solo funciona en Windows (el atajo global usa la API nativa de Windows).
- Todo el procesamiento ocurre en tu computadora: no se envía nada a internet.
- Si querés que se vea un ícono propio, poné un archivo `icono.ico` junto al script.

## Licencia

[MIT](LICENSE). Se ofrece "tal cual", sin garantías.
