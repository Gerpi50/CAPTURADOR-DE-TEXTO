"""
CapturarTexto.py - Captura texto de cualquier parte de la pantalla con OCR
===========================================================================

Instalación (ejecutar UNA sola vez):
    pip install pillow pytesseract pyperclip

También necesitás instalar Tesseract OCR (el programa que lee el texto):
    1. Descargalo de: https://github.com/UB-Mannheim/tesseract/wiki
    2. En el instalador, abrí "Additional language data" y tildá "Spanish".
    3. Instalalo en la ruta por defecto: C:\\Program Files\\Tesseract-OCR\\

Uso:
    - Ejecutá este script.
    - Presioná Ctrl+Shift+Z (funciona desde cualquier programa) o el botón de la ventana.
    - La pantalla se congela: dibujá un rectángulo sobre el texto que querés copiar.
      Para cancelar: ESC o clic derecho.
    - El texto aparece en la ventana y se copia solo al portapapeles.

Para cambiar el atajo de teclado, editá ATAJOS_A_PROBAR (más abajo).
"""

import os
import queue
import shutil
import sys
import threading
import tkinter as tk
import traceback
from concurrent.futures import ThreadPoolExecutor
from tkinter import messagebox

ES_WINDOWS = sys.platform == "win32"
if ES_WINDOWS:
    import ctypes
    from ctypes import wintypes

TITULO = "GP Capturar Texto de Pantalla"


def _salir_por_libreria_faltante(error):
    """Avisa con una ventanita (no todos ejecutan desde una consola) y cierra."""
    detalle = getattr(error, "name", None) or str(error)
    mensaje = (
        f"Falta instalar una librería de Python ({detalle}).\n\n"
        "Abrí una consola (cmd) y ejecutá:\n\n"
        "    pip install pillow pytesseract pyperclip"
    )
    try:
        raiz = tk.Tk()
        raiz.withdraw()
        messagebox.showerror(TITULO, mensaje)
        raiz.destroy()
    except Exception:
        print(mensaje)
    sys.exit(1)


try:
    import pytesseract
    from PIL import Image, ImageFilter, ImageGrab, ImageOps, ImageTk
except ImportError as error_importacion:
    _salir_por_libreria_faltante(error_importacion)

try:
    import pyperclip
except ImportError:
    pyperclip = None  # es opcional: si falta se usa el portapapeles de Tkinter


# ── Configuración ─────────────────────────────────────────────────────────────
APP_ID = "GP.CapturarTexto.1"            # identidad de la app en la barra de tareas
NOMBRE_MUTEX = "GP.CapturarTexto.Mutex"  # evita abrir dos copias a la vez

# Atajos globales a probar, en orden: se usa el primero que no esté ocupado.
# Formato: Ctrl / Shift / Alt / Win  +  una letra, un número o una tecla F1…F24.
ATAJOS_A_PROBAR = ("Ctrl+Shift+Z", "Ctrl+Shift+X")

IDIOMAS = (
    ("Español + Inglés (recomendado)", "spa+eng"),
    ("Solo Español", "spa"),
    ("Solo Inglés", "eng"),
)

LADO_MINIMO_PX = 10        # las selecciones más chicas se descartan (fue un clic suelto)
RETARDO_OCULTAR_MS = 200   # espera a que la ventana desaparezca antes de sacar la foto
INTERVALO_COLA_MS = 50     # cada cuánto la ventana revisa los avisos de los otros hilos

# OCR
MODOS_PSM = (6, 4, 11, 3)  # 6 bloque uniforme · 4 columnas · 11 texto disperso · 3 automático
MAX_PIXELES_OCR = 6_000_000
BORDE_OCR_PX = 20
TIMEOUT_OCR_S = 60

# Colores (paleta Catppuccin Mocha)
COLOR_FONDO = "#1e1e2e"
COLOR_PANEL = "#313244"
COLOR_AREA_TEXTO = "#181825"
COLOR_SELECCION = "#45475a"
COLOR_TEXTO = "#cdd6f4"
COLOR_TEXTO_SUAVE = "#a6adc8"
COLOR_AZUL = "#89b4fa"
COLOR_AZUL_CLARO = "#74c7ec"
COLOR_VERDE = "#a6e3a1"
COLOR_AMARILLO = "#f9e2af"
COLOR_ROJO = "#f38ba8"
COLOR_ROSA = "#f5c2e7"
COLORES_ESTADO = {
    "info": COLOR_TEXTO_SUAVE,
    "success": COLOR_VERDE,
    "warning": COLOR_AMARILLO,
    "error": COLOR_ROJO,
}

# Constantes de la API de Windows
MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x0001, 0x0002, 0x0004, 0x0008, 0x4000
WM_HOTKEY, WM_QUIT, WM_USER, PM_NOREMOVE = 0x0312, 0x0012, 0x0400, 0x0000
VK_ESCAPE = 0x1B
ERROR_ALREADY_EXISTS = 183


# ── Rutas: Tesseract e ícono ──────────────────────────────────────────────────
def _carpetas_base():
    """Carpetas donde buscar archivos propios (funciona como script y como .exe)."""
    carpetas = []
    if getattr(sys, "frozen", False):
        carpetas.append(getattr(sys, "_MEIPASS", ""))
        carpetas.append(os.path.dirname(sys.executable))
    else:
        carpetas.append(os.path.dirname(os.path.abspath(__file__)))
    return [c for c in carpetas if c]


CARPETAS_BASE = _carpetas_base()


def ruta_icono():
    """Ruta al .ico, tanto en desarrollo como dentro del .exe empaquetado."""
    for base in CARPETAS_BASE:
        for nombre in ("icono.ico", "icon.ico"):
            ruta = os.path.join(base, nombre)
            if os.path.isfile(ruta):
                return ruta
    return None


def encontrar_tesseract():
    """Busca tesseract: primero junto al programa, después donde lo instala Windows, al final en el PATH."""
    exe = "tesseract.exe" if ES_WINDOWS else "tesseract"
    candidatos = []
    for base in CARPETAS_BASE:
        candidatos.append(os.path.join(base, "tesseract", exe))
        candidatos.append(os.path.join(base, "Tesseract-OCR", exe))
    for variable in ("ProgramW6432", "ProgramFiles", "ProgramFiles(x86)"):
        carpeta = os.environ.get(variable)
        if carpeta:
            candidatos.append(os.path.join(carpeta, "Tesseract-OCR", exe))
    local = os.environ.get("LOCALAPPDATA")  # instalación "solo para mí"
    if local:
        candidatos.append(os.path.join(local, "Programs", "Tesseract-OCR", exe))
    en_path = shutil.which("tesseract")
    if en_path:
        candidatos.append(en_path)
    return next((ruta for ruta in candidatos if os.path.isfile(ruta)), None)


# ── Detalles de Windows ───────────────────────────────────────────────────────
def habilitar_dpi_awareness():
    """CRÍTICO con escalado 125 % / 150 %: sin esto las coordenadas del mouse no coinciden
    con los píxeles reales de la pantalla y la captura sale corrida."""
    if not ES_WINDOWS:
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # por monitor (Windows 8.1+)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()   # alternativa para versiones viejas
        except Exception:
            pass


def fijar_app_id():
    """Sin esto Windows agrupa la ventana bajo el ícono genérico de Python."""
    if not ES_WINDOWS:
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except Exception:
        pass


_mutex_instancia = None  # se guarda para que Windows lo mantenga vivo mientras corre el programa


def es_instancia_unica():
    """False si ya hay otra copia del programa abierta (ahí se perdería el atajo)."""
    global _mutex_instancia
    if not ES_WINDOWS:
        return True
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        _mutex_instancia = kernel32.CreateMutexW(None, False, NOMBRE_MUTEX)
        return ctypes.get_last_error() != ERROR_ALREADY_EXISTS
    except Exception:
        return True


def geometria_escritorio_virtual(raiz):
    """(x, y, ancho, alto) del escritorio completo, sumando todos los monitores."""
    if ES_WINDOWS:
        try:
            medir = ctypes.windll.user32.GetSystemMetrics
            x, y, ancho, alto = medir(76), medir(77), medir(78), medir(79)  # SM_*VIRTUALSCREEN
            if ancho > 0 and alto > 0:
                return x, y, ancho, alto
        except Exception:
            pass
    return 0, 0, raiz.winfo_screenwidth(), raiz.winfo_screenheight()


def tecla_presionada(codigo_vk):
    """True si la tecla está apretada ahora mismo, en cualquier programa."""
    if not ES_WINDOWS:
        return False
    try:
        return bool(ctypes.windll.user32.GetAsyncKeyState(codigo_vk) & 0x8000)
    except Exception:
        return False


# ── Atajo de teclado global ───────────────────────────────────────────────────
_MODIFICADORES = {
    "ctrl": MOD_CONTROL, "control": MOD_CONTROL,
    "shift": MOD_SHIFT, "alt": MOD_ALT, "win": MOD_WIN,
}


def parsear_atajo(texto):
    """Convierte 'Ctrl+Shift+Z' en (modificadores, código de tecla virtual de Windows)."""
    modificadores = 0
    tecla = None
    es_funcion = False
    for parte in (p.strip().lower() for p in texto.split("+")):
        if parte in _MODIFICADORES:
            modificadores |= _MODIFICADORES[parte]
        elif len(parte) == 1 and parte.isascii() and parte.isalnum():
            tecla = ord(parte.upper())                  # A-Z y 0-9: el código es el del carácter
        elif parte[:1] == "f" and parte[1:].isdigit() and 1 <= int(parte[1:]) <= 24:
            tecla = 0x70 + int(parte[1:]) - 1           # F1 = 0x70
            es_funcion = True
        else:
            raise ValueError(f"Atajo no válido: {texto!r}")
    if tecla is None or not (modificadores or es_funcion):
        raise ValueError(f"Atajo no válido: {texto!r} (necesita una tecla y algún modificador)")
    return modificadores, tecla


class AtajoGlobal:
    """Atajo de teclado global: funciona aunque otro programa tenga el foco.

    Usa RegisterHotKey, la función nativa de Windows: no necesita permisos de
    administrador ni librerías extra. El aviso llega desde un hilo aparte, así que
    el callback NO debe tocar Tkinter (acá solo deja un mensaje en una cola).
    """

    _ID = 0x4750  # número cualquiera para identificar nuestro atajo

    def __init__(self, texto, callback):
        self.texto = texto
        self.callback = callback
        self.modificadores, self.tecla = parsear_atajo(texto)
        self.activo = False
        self.error = None
        self._hilo_id = None
        self._listo = threading.Event()

    def iniciar(self):
        """Registra el atajo. Devuelve True si quedó funcionando."""
        threading.Thread(target=self._bucle, name="AtajoGlobal", daemon=True).start()
        self._listo.wait(timeout=3)
        return self.activo

    def detener(self):
        if self.activo and self._hilo_id:
            ctypes.windll.user32.PostThreadMessageW(self._hilo_id, WM_QUIT, 0, 0)

    def _bucle(self):
        try:
            self._escuchar()
        except Exception as exc:  # falla inesperada de ctypes: el atajo queda inactivo
            self.error = exc
        finally:
            self.activo = False
            self._listo.set()

    def _escuchar(self):
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32")
        puntero_msg = ctypes.POINTER(wintypes.MSG)

        user32.RegisterHotKey.argtypes = (wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT)
        user32.RegisterHotKey.restype = wintypes.BOOL
        user32.UnregisterHotKey.argtypes = (wintypes.HWND, ctypes.c_int)
        user32.UnregisterHotKey.restype = wintypes.BOOL
        user32.PeekMessageW.argtypes = (puntero_msg, wintypes.HWND, wintypes.UINT,
                                        wintypes.UINT, wintypes.UINT)
        user32.PeekMessageW.restype = wintypes.BOOL
        user32.GetMessageW.argtypes = (puntero_msg, wintypes.HWND, wintypes.UINT, wintypes.UINT)
        user32.GetMessageW.restype = ctypes.c_int
        kernel32.GetCurrentThreadId.restype = wintypes.DWORD

        mensaje = wintypes.MSG()
        # Crea la cola de mensajes de este hilo (hace falta para recibir WM_HOTKEY y WM_QUIT).
        user32.PeekMessageW(ctypes.byref(mensaje), None, WM_USER, WM_USER, PM_NOREMOVE)
        self._hilo_id = kernel32.GetCurrentThreadId()

        if not user32.RegisterHotKey(None, self._ID, self.modificadores | MOD_NOREPEAT, self.tecla):
            return  # otro programa ya usa esa combinación
        self.activo = True
        self._listo.set()
        try:
            while user32.GetMessageW(ctypes.byref(mensaje), None, 0, 0) > 0:
                if mensaje.message == WM_HOTKEY and mensaje.wParam == self._ID:
                    self.callback()
        finally:
            user32.UnregisterHotKey(None, self._ID)


# ── Preprocesado de imagen y OCR ──────────────────────────────────────────────
def _umbral_otsu(img_gris):
    """Calcula el umbral óptimo de Otsu a partir del histograma (solo PIL, sin numpy)."""
    histograma = img_gris.histogram()
    total = sum(histograma)
    if total == 0:
        return 128
    suma_total = sum(i * histograma[i] for i in range(256))
    suma_fondo = 0.0
    peso_fondo = 0
    max_varianza = -1.0
    umbral = 128
    for i in range(256):
        peso_fondo += histograma[i]
        if peso_fondo == 0:
            continue
        peso_frente = total - peso_fondo
        if peso_frente == 0:
            break
        suma_fondo += i * histograma[i]
        media_fondo = suma_fondo / peso_fondo
        media_frente = (suma_total - suma_fondo) / peso_frente
        varianza = peso_fondo * peso_frente * (media_fondo - media_frente) ** 2  # entre clases
        if varianza > max_varianza:
            max_varianza = varianza
            umbral = i
    return umbral


def elegir_escala(ancho, alto):
    """Agranda más las áreas chicas y menos las grandes, para que el OCR no tarde una eternidad."""
    escala = 3
    while escala > 1 and ancho * alto * escala * escala > MAX_PIXELES_OCR:
        escala -= 1
    return escala


def preparar_imagen(imagen):
    """Mejora la imagen para el OCR: grises → contraste → agrandar → nitidez → blanco y negro."""
    gris = ImageOps.autocontrast(imagen.convert("L"), cutoff=2)
    escala = elegir_escala(*gris.size)
    if escala > 1:  # Tesseract rinde mejor con letras de ~30 px de alto
        gris = gris.resize((gris.width * escala, gris.height * escala), Image.LANCZOS)
    gris = gris.filter(ImageFilter.SHARPEN)

    umbral = _umbral_otsu(gris)
    bn = gris.point(lambda p: 255 if p > umbral else 0)

    # Texto claro sobre fondo oscuro: si predomina el negro, se invierte (texto negro sobre blanco).
    histograma = bn.histogram()
    if histograma[0] > histograma[255]:
        bn = ImageOps.invert(bn)

    # Un borde blanco ayuda cuando el texto queda pegado al límite de la selección.
    return ImageOps.expand(bn, border=BORDE_OCR_PX, fill=255)


def _puntaje(texto):
    """Más texto 'real' (palabras con 2+ letras o números) es mejor; ignora símbolos sueltos."""
    return sum(len(palabra) for palabra in texto.split()
               if sum(c.isalnum() for c in palabra) >= 2)


def _leer(imagen, idioma, psm):
    texto = pytesseract.image_to_string(
        imagen, lang=idioma, config=f"--psm {psm} --oem 3", timeout=TIMEOUT_OCR_S
    )
    return texto.replace("\r\n", "\n").replace("\r", "\n").strip()


def ocr_multi_psm(imagen, idioma):
    """Prueba varios modos de segmentación (PSM) a la vez y devuelve el mejor resultado."""
    resultados = []
    errores = []
    with ThreadPoolExecutor(max_workers=len(MODOS_PSM)) as pool:
        futuros = [pool.submit(_leer, imagen, idioma, psm) for psm in MODOS_PSM]
        for futuro in futuros:
            try:
                texto = futuro.result()
            except Exception as exc:  # un modo puede fallar sin arruinar a los demás
                errores.append(exc)
                continue
            if texto:
                resultados.append(texto)
    if resultados:
        return max(resultados, key=_puntaje)  # ante un empate gana el primero (PSM 6)
    if len(errores) == len(MODOS_PSM):
        raise errores[0]  # fallaron todos: no es "sin texto", es un problema real
    return ""


# ── Selector de área ──────────────────────────────────────────────────────────
class SelectorPantalla:
    """Pantalla "congelada" a pantalla completa donde se dibuja el rectángulo a capturar.

    La foto de toda la pantalla se saca ANTES de mostrar esta ventana, así que el velo
    nunca sale en la captura, funciona con varios monitores y también con menús o
    tooltips que desaparecerían si se cambiara de ventana.
    """

    def __init__(self, master, foto, al_elegir, al_cancelar):
        self.master = master
        self.foto = foto
        self.al_elegir = al_elegir
        self.al_cancelar = al_cancelar
        self.inicio = None
        self.rect = None
        self.terminado = False

        x, y, ancho, alto = geometria_escritorio_virtual(master)
        self.ventana = tk.Toplevel(master)
        self.ventana.overrideredirect(True)
        self.ventana.attributes("-topmost", True)
        self.ventana.geometry(f"{ancho}x{alto}+{x}+{y}")  # con monitores a la izquierda x es negativo

        self.canvas = tk.Canvas(
            self.ventana, width=ancho, height=alto, bg="black",
            highlightthickness=0, bd=0, cursor="crosshair",
        )
        self.canvas.pack(fill="both", expand=True)

        fondo = foto if foto.size == (ancho, alto) else foto.resize((ancho, alto), Image.BILINEAR)
        self._fondo = ImageTk.PhotoImage(fondo)  # hay que guardar la referencia o Tk la descarta
        self.canvas.create_image(0, 0, image=self._fondo, anchor="nw")
        self._dibujar_instrucciones(-x + master.winfo_screenwidth() // 2)  # centro del monitor principal

        self.canvas.bind("<ButtonPress-1>", self._presionar)
        self.canvas.bind("<B1-Motion>", self._arrastrar)
        self.canvas.bind("<ButtonRelease-1>", self._soltar)
        self.canvas.bind("<Button-3>", self.cancelar)
        self.ventana.bind("<Escape>", self.cancelar)

        self.ventana.lift()
        self.ventana.focus_force()
        self._vigilar_esc()

    def _dibujar_instrucciones(self, centro_x):
        texto = self.canvas.create_text(
            centro_x, 40, fill="white", font=("Segoe UI", 14, "bold"),
            text="Dibujá un rectángulo sobre el texto que querés capturar   ·   "
                 "ESC o clic derecho para cancelar",
        )
        x0, y0, x1, y1 = self.canvas.bbox(texto)
        fondo = self.canvas.create_rectangle(
            x0 - 14, y0 - 8, x1 + 14, y1 + 8, fill=COLOR_FONDO, outline=COLOR_AZUL, width=2
        )
        self.canvas.tag_lower(fondo, texto)

    def _vigilar_esc(self):
        """Windows no siempre le da el foco del teclado a una ventana sin bordes,
        así que ESC se mira directamente en lugar de esperar el evento de Tk."""
        if self.terminado:
            return
        if tecla_presionada(VK_ESCAPE):
            self.cancelar()
            return
        self.master.after(30, self._vigilar_esc)

    def _limitar(self, x, y):
        """Mantiene el punto dentro de la pantalla aunque el mouse se salga mientras se arrastra."""
        return (min(max(x, 0), self.canvas.winfo_width()),
                min(max(y, 0), self.canvas.winfo_height()))

    def _borrar_seleccion(self):
        if self.rect is not None:
            self.canvas.delete(self.rect)
        self.rect = None
        self.inicio = None

    def _presionar(self, evento):
        self._borrar_seleccion()
        self.inicio = self._limitar(evento.x, evento.y)

    def _arrastrar(self, evento):
        if self.inicio is None:
            return
        x0, y0 = self.inicio
        x1, y1 = self._limitar(evento.x, evento.y)
        if self.rect is None:
            self.rect = self.canvas.create_rectangle(
                x0, y0, x1, y1, outline="#00ff88", width=3, fill="#00ff88", stipple="gray12"
            )
        else:
            self.canvas.coords(self.rect, x0, y0, x1, y1)

    def _soltar(self, evento):
        if self.inicio is None:
            return
        x0, y0 = self.inicio
        x1, y1 = self._limitar(evento.x, evento.y)
        izquierda, derecha = sorted((x0, x1))
        arriba, abajo = sorted((y0, y1))
        if derecha - izquierda < LADO_MINIMO_PX or abajo - arriba < LADO_MINIMO_PX:
            self._borrar_seleccion()  # fue un clic suelto: se puede volver a intentar
            return
        recorte = self._recortar(izquierda, arriba, derecha, abajo)
        self._cerrar()
        self.al_elegir(recorte)

    def _recortar(self, izquierda, arriba, derecha, abajo):
        """Pasa de coordenadas de la ventana a píxeles de la foto y recorta."""
        factor_x = self.foto.width / max(self.canvas.winfo_width(), 1)
        factor_y = self.foto.height / max(self.canvas.winfo_height(), 1)
        caja = (
            max(round(izquierda * factor_x), 0),
            max(round(arriba * factor_y), 0),
            min(round(derecha * factor_x), self.foto.width),
            min(round(abajo * factor_y), self.foto.height),
        )
        return self.foto.crop(caja)

    def _cerrar(self):
        self.terminado = True
        try:
            self.ventana.destroy()
        except tk.TclError:
            pass

    def cancelar(self, _evento=None):
        if self.terminado:
            return
        self._cerrar()
        self.al_cancelar()


# ── Aplicación principal ──────────────────────────────────────────────────────
class CapturarTexto:
    """Ventana principal."""

    def __init__(self):
        # Los hilos (atajo y OCR) nunca tocan Tkinter: dejan avisos en esta cola y la
        # ventana los lee desde su propio hilo (_revisar_cola).
        self.cola = queue.Queue()
        self.tesseract_ok = False
        self.idiomas_instalados = set()
        self.capturando = False
        self.selector = None
        self.atajo = None

        self.root = tk.Tk()
        self.root.title(f"📸 {TITULO}")
        self.root.configure(bg=COLOR_FONDO)
        factor = max(self.root.winfo_fpixels("1i") / 96.0, 1.0)  # tamaño acorde al escalado de Windows
        self.root.geometry(f"{int(750 * factor)}x{int(560 * factor)}")
        self.root.minsize(int(560 * factor), int(420 * factor))
        self._poner_icono()
        self.root.protocol("WM_DELETE_WINDOW", self.cerrar)
        self.root.report_callback_exception = self._error_inesperado

        self.crear_interfaz()
        self.verificar_tesseract()
        self.registrar_atajo()
        self.mostrar_estado_inicial()
        self.root.after(INTERVALO_COLA_MS, self._revisar_cola)

    def ejecutar(self):
        self.root.mainloop()

    def cerrar(self):
        if self.atajo:
            self.atajo.detener()
        self.root.destroy()

    def _poner_icono(self):
        ico = ruta_icono()
        if ico:
            try:
                self.root.iconbitmap(ico)
            except tk.TclError:
                pass

    # ── Interfaz ──────────────────────────────────────────────────────────────
    def crear_interfaz(self):
        # Encabezado
        encabezado = tk.Frame(self.root, bg=COLOR_PANEL, pady=12)
        encabezado.pack(fill="x")
        tk.Label(
            encabezado, text=f"📸 {TITULO}", font=("Segoe UI", 15, "bold"),
            bg=COLOR_PANEL, fg=COLOR_TEXTO,
        ).pack(side="left", padx=20)
        self.lbl_tesseract = tk.Label(
            encabezado, text="⏳ Verificando OCR...", font=("Segoe UI", 9),
            bg=COLOR_PANEL, fg=COLOR_AMARILLO,
        )
        self.lbl_tesseract.pack(side="right", padx=20)

        # La barra de estado y los botones de abajo se empaquetan primero para que,
        # si la ventana se achica, el área de texto ceda lugar en vez de taparlos.
        self.status = tk.Label(
            self.root, text="", bg=COLOR_PANEL, fg=COLOR_TEXTO_SUAVE,
            font=("Segoe UI", 9), anchor="w", pady=5, padx=10,
        )
        self.status.pack(fill="x", side="bottom")

        botones = tk.Frame(self.root, bg=COLOR_FONDO, pady=8)
        botones.pack(fill="x", side="bottom", padx=20)
        tk.Button(
            botones, text="📋 Copiar al portapapeles", command=self.copiar_resultado,
            bg=COLOR_PANEL, fg=COLOR_VERDE, font=("Segoe UI", 9, "bold"),
            relief="flat", padx=12, pady=6, cursor="hand2",
        ).pack(side="left", padx=(0, 8))
        tk.Button(
            botones, text="🗑️ Limpiar", command=self.limpiar,
            bg=COLOR_PANEL, fg=COLOR_ROJO, font=("Segoe UI", 9),
            relief="flat", padx=12, pady=6, cursor="hand2",
        ).pack(side="left")

        # Botón principal (el texto con el atajo se completa en registrar_atajo)
        self.btn_capturar = tk.Button(
            self.root, text="🎯  CAPTURAR TEXTO", command=self.iniciar_captura,
            bg=COLOR_AZUL, fg=COLOR_FONDO, font=("Segoe UI", 13, "bold"),
            relief="flat", pady=14, cursor="hand2",
            activebackground=COLOR_AZUL_CLARO, activeforeground=COLOR_FONDO,
        )
        self.btn_capturar.pack(fill="x", padx=20, pady=15)

        # Idioma
        marco_idioma = tk.Frame(self.root, bg=COLOR_FONDO)
        marco_idioma.pack(fill="x", padx=20, pady=(0, 8))
        tk.Label(
            marco_idioma, text="Idioma del texto:",
            bg=COLOR_FONDO, fg=COLOR_TEXTO_SUAVE, font=("Segoe UI", 9),
        ).pack(side="left")
        self.var_idioma = tk.StringVar(value=IDIOMAS[0][1])
        for texto, valor in IDIOMAS:
            tk.Radiobutton(
                marco_idioma, text=texto, variable=self.var_idioma, value=valor,
                bg=COLOR_FONDO, fg=COLOR_TEXTO, selectcolor=COLOR_PANEL,
                activebackground=COLOR_FONDO, activeforeground=COLOR_TEXTO,
                font=("Segoe UI", 9),
            ).pack(side="left", padx=10)

        # Resultado
        tk.Label(
            self.root, text="Texto capturado:",
            bg=COLOR_FONDO, fg=COLOR_AZUL, font=("Segoe UI", 9, "bold"),
        ).pack(anchor="w", padx=20)

        marco_texto = tk.Frame(self.root, bg=COLOR_FONDO)
        marco_texto.pack(fill="both", expand=True, padx=20, pady=(4, 0))
        scroll_y = tk.Scrollbar(marco_texto)
        scroll_y.pack(side="right", fill="y")
        scroll_x = tk.Scrollbar(marco_texto, orient="horizontal")
        scroll_x.pack(side="bottom", fill="x")
        self.text_area = tk.Text(
            marco_texto, wrap="none", font=("Courier New", 11),
            bg=COLOR_AREA_TEXTO, fg=COLOR_TEXTO, insertbackground=COLOR_ROSA,
            selectbackground=COLOR_SELECCION, relief="flat", padx=10, pady=10, undo=True,
            yscrollcommand=scroll_y.set, xscrollcommand=scroll_x.set,
        )
        self.text_area.pack(fill="both", expand=True)
        scroll_y.config(command=self.text_area.yview)
        scroll_x.config(command=self.text_area.xview)

    def set_status(self, mensaje, tipo="info"):
        self.status.config(text=mensaje, fg=COLORES_ESTADO.get(tipo, COLOR_TEXTO_SUAVE))

    # ── Tesseract y atajo ─────────────────────────────────────────────────────
    def verificar_tesseract(self):
        ruta = encontrar_tesseract()
        if not ruta:
            self.tesseract_ok = False
            self.lbl_tesseract.config(text="❌ Tesseract no encontrado", fg=COLOR_ROJO)
            self.mostrar_instrucciones_tesseract()
            return
        pytesseract.pytesseract.tesseract_cmd = ruta
        self.tesseract_ok = True
        try:
            self.idiomas_instalados = set(pytesseract.get_languages(config=""))
        except Exception:
            self.idiomas_instalados = {"eng"}
        if "spa" in self.idiomas_instalados:
            self.lbl_tesseract.config(text="✅ OCR listo", fg=COLOR_VERDE)
        else:
            self.lbl_tesseract.config(text="✅ OCR (⚠ sin español)", fg=COLOR_AMARILLO)

    def mostrar_instrucciones_tesseract(self):
        self.text_area.insert(
            "end",
            "⚠️  NECESITÁS INSTALAR TESSERACT OCR\n"
            "════════════════════════════════════\n\n"
            "1. Entrá a este link y descargá el instalador (.exe):\n"
            "   https://github.com/UB-Mannheim/tesseract/wiki\n\n"
            "2. En el instalador abrí \"Additional language data\" y tildá \"Spanish\".\n\n"
            "3. Instalalo en la ruta por defecto:\n"
            "   C:\\Program Files\\Tesseract-OCR\\\n\n"
            "4. Reiniciá este programa.\n\n"
            "5. ¡Listo! Ya podés capturar texto de cualquier pantalla.\n",
        )
        self.text_area.config(fg=COLOR_AMARILLO)

    def resolver_idioma(self):
        """Del idioma elegido deja solo los que están instalados (si no hay ninguno, inglés)."""
        pedidos = self.var_idioma.get().split("+")
        disponibles = [i for i in pedidos if i in self.idiomas_instalados]
        if not disponibles:
            disponibles = ["eng"]
        return "+".join(disponibles)

    def registrar_atajo(self):
        """Activa el atajo global (solo Windows): prueba cada opción hasta que una funcione."""
        texto_boton = "🎯  CAPTURAR TEXTO"
        if ES_WINDOWS:
            for texto in ATAJOS_A_PROBAR:
                try:
                    atajo = AtajoGlobal(texto, lambda: self.cola.put(("atajo", None)))
                except ValueError:
                    continue  # atajo mal escrito en ATAJOS_A_PROBAR
                if atajo.iniciar():
                    self.atajo = atajo
                    texto_boton += f"  ({texto})"
                    break
        self.btn_capturar.config(text=texto_boton)

    def mostrar_estado_inicial(self):
        if not self.tesseract_ok:
            self.set_status("❌ Falta instalar Tesseract OCR (mirá las instrucciones de abajo).", "error")
            return
        avisos = []
        if "spa" not in self.idiomas_instalados:
            avisos.append("Falta el idioma Español de Tesseract: reinstalalo y tildá «Spanish»")
        if ES_WINDOWS and self.atajo is None:
            avisos.append("El atajo de teclado no está disponible (lo usa otro programa)")
        if avisos:
            self.set_status("⚠️ " + " · ".join(avisos), "warning")
        elif self.atajo:
            self.set_status(
                f"✅ Listo. Presioná {self.atajo.texto} (desde cualquier programa) "
                "o el botón para capturar.", "success")
        else:
            self.set_status("✅ Listo. Presioná el botón para capturar texto.", "success")

    # ── Avisos de los otros hilos ─────────────────────────────────────────────
    def _revisar_cola(self):
        try:
            while True:
                try:
                    evento, dato = self.cola.get_nowait()
                except queue.Empty:
                    break
                self._despachar(evento, dato)
        finally:  # pase lo que pase, la revisión sigue funcionando
            self.root.after(INTERVALO_COLA_MS, self._revisar_cola)

    def _despachar(self, evento, dato):
        if evento == "atajo":
            self.iniciar_captura()
        elif evento == "ocr_ok":
            self._liberar_captura()
            self.mostrar_resultado(dato)
        elif evento == "ocr_error":
            self._liberar_captura()
            self.set_status(f"❌ Error al procesar el OCR: {dato}"[:220], "error")

    # ── Flujo de captura ──────────────────────────────────────────────────────
    def iniciar_captura(self):
        if self.capturando:
            return
        if not self.tesseract_ok:
            messagebox.showwarning(
                "Tesseract no instalado",
                "Instalá Tesseract OCR primero.\nVer instrucciones en la ventana principal.",
            )
            return
        self.capturando = True
        self.btn_capturar.config(state="disabled")
        self.set_status("🎯 Dibujá un rectángulo sobre el texto...")
        self.root.withdraw()  # que la ventana no salga en la foto
        self.root.after(RETARDO_OCULTAR_MS, self._congelar_y_elegir)

    def _congelar_y_elegir(self):
        try:
            foto = ImageGrab.grab(include_layered_windows=True, all_screens=True)
            self.selector = SelectorPantalla(
                self.root, foto, self._area_elegida, self._captura_cancelada
            )
        except Exception as exc:
            self.selector = None
            self._restaurar_ventana()
            self._liberar_captura()
            self.set_status(f"❌ No se pudo preparar la captura: {exc}"[:220], "error")

    def _area_elegida(self, recorte):
        self.selector = None
        self._restaurar_ventana()
        self.set_status("⏳ Procesando OCR...")
        idioma = self.resolver_idioma()  # se lee acá porque los hilos no pueden tocar Tkinter
        threading.Thread(target=self._trabajo_ocr, args=(recorte, idioma), daemon=True).start()

    def _captura_cancelada(self):
        self.selector = None
        self._restaurar_ventana()
        self._liberar_captura()
        self.set_status("Captura cancelada.")

    def _trabajo_ocr(self, recorte, idioma):
        """Corre en un hilo aparte: no toca Tkinter, solo deja el resultado en la cola."""
        try:
            texto = ocr_multi_psm(preparar_imagen(recorte), idioma)
            self.cola.put(("ocr_ok", texto))
        except Exception as exc:
            self.cola.put(("ocr_error", f"{type(exc).__name__}: {exc}"))

    def _liberar_captura(self):
        self.capturando = False
        self.btn_capturar.config(state="normal")

    def _restaurar_ventana(self):
        """Vuelve a mostrar la ventana, por delante de las demás."""
        self.root.deiconify()
        self.root.lift()
        self.root.attributes("-topmost", True)
        self.root.after(300, self._quitar_topmost)
        self.root.focus_force()

    def _quitar_topmost(self):
        try:
            self.root.attributes("-topmost", False)
        except tk.TclError:
            pass

    def _error_inesperado(self, tipo, valor, rastro):
        """Red de seguridad: si algo falla a mitad de una captura, la ventana no queda oculta."""
        if sys.stderr:  # sin consola (pythonw / .exe sin ventana) no hay dónde imprimir
            traceback.print_exception(tipo, valor, rastro)
        try:
            self.selector = None
            self._restaurar_ventana()
            self._liberar_captura()
            self.set_status(f"❌ Error inesperado: {valor}"[:220], "error")
        except tk.TclError:
            pass

    # ── Resultado ─────────────────────────────────────────────────────────────
    def mostrar_resultado(self, texto):
        self.text_area.delete("1.0", "end")
        if not texto.strip():
            self.text_area.insert("end", "(No se detectó texto en el área seleccionada)")
            self.text_area.config(fg=COLOR_AMARILLO)
            self.set_status(
                "⚠️ No se detectó texto. Probá con un área un poco más grande o con otro idioma.",
                "warning",
            )
            return  # no se toca el portapapeles
        self.text_area.config(fg=COLOR_TEXTO)
        self.text_area.insert("end", texto)
        copiado = " · Copiado al portapapeles automáticamente ✓" if self.copiar_al_portapapeles(texto) else ""
        self.set_status(
            f"✅ Capturado: {len(texto)} caracteres, {texto.count(chr(10)) + 1} líneas{copiado}",
            "success",
        )

    def copiar_al_portapapeles(self, texto):
        """Copia el texto. Devuelve True si se pudo."""
        if pyperclip is not None:
            try:
                pyperclip.copy(texto)
                return True
            except Exception:
                pass
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(texto)
            return True
        except tk.TclError:
            return False

    def copiar_resultado(self):
        texto = self.text_area.get("1.0", "end-1c")
        if not texto.strip():
            self.set_status("⚠️ No hay texto para copiar.", "warning")
        elif self.copiar_al_portapapeles(texto):
            self.set_status("✅ Copiado al portapapeles.", "success")
        else:
            self.set_status("❌ No se pudo acceder al portapapeles.", "error")

    def limpiar(self):
        self.text_area.delete("1.0", "end")
        self.text_area.config(fg=COLOR_TEXTO)
        self.set_status("Listo para una nueva captura.")


# ── Punto de entrada ──────────────────────────────────────────────────────────
def main():
    # Estos dos tienen que ir ANTES de crear cualquier ventana de Tkinter.
    habilitar_dpi_awareness()
    fijar_app_id()

    if not es_instancia_unica():
        raiz = tk.Tk()
        raiz.withdraw()
        messagebox.showinfo(TITULO, "El programa ya está abierto.\n\nBuscalo en la barra de tareas.")
        raiz.destroy()
        return

    CapturarTexto().ejecutar()


if __name__ == "__main__":
    main()
