"""
Freno a intentos de inicio de sesion fallidos.

Cuenta los fallos por IP y por correo durante una ventana de tiempo. Al
llegar al limite, el inicio de sesion se rechaza ANTES de verificar la
contrasena, durante el resto de la ventana. Asi un ataque de fuerza bruta
no puede probar miles de contrasenas contra una cuenta, ni una IP puede
probar muchas cuentas.

Se apoya en el cache de Django. Con el cache por defecto (memoria local)
cada proceso de gunicorn lleva su propia cuenta; con ``DJANGO_CACHE_URL``
apuntando a Redis la cuenta es compartida entre procesos y servidores
(ver config/settings.py). Si el cache falla, el login NO se bloquea: se
prefiere dejar entrar a un usuario legitimo que tumbar el acceso de todos.
"""

import hashlib
import logging

from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

MAX_FALLOS_POR_CORREO = getattr(settings, "LOGIN_MAX_FALLOS_POR_CORREO", 5)
MAX_FALLOS_POR_IP = getattr(settings, "LOGIN_MAX_FALLOS_POR_IP", 20)
VENTANA_SEGUNDOS = getattr(settings, "LOGIN_VENTANA_BLOQUEO_SEGUNDOS", 15 * 60)


def _clave(tipo, valor):
    # El correo y la IP se guardan con hash: el cache nunca contiene datos personales en claro.
    digest = hashlib.sha256(f"{tipo}:{valor}".encode("utf-8")).hexdigest()[:32]
    return f"login-fallos:{tipo}:{digest}"


def _claves(correo, ip):
    claves = []
    if correo:
        claves.append((_clave("correo", correo.strip().lower()), MAX_FALLOS_POR_CORREO))
    if ip:
        claves.append((_clave("ip", ip), MAX_FALLOS_POR_IP))
    return claves


def esta_bloqueado(correo, ip):
    try:
        for clave, limite in _claves(correo, ip):
            if (cache.get(clave) or 0) >= limite:
                return True
    except Exception:  # noqa: BLE001 - un cache caido no debe impedir el acceso
        logger.warning("No se pudo consultar el freno de inicio de sesion", exc_info=True)
    return False


def registrar_fallo(correo, ip):
    try:
        for clave, _limite in _claves(correo, ip):
            if cache.add(clave, 1, VENTANA_SEGUNDOS):
                continue
            try:
                cache.incr(clave)
            except ValueError:
                # La clave expiro entre add() e incr().
                cache.set(clave, 1, VENTANA_SEGUNDOS)
    except Exception:  # noqa: BLE001
        logger.warning("No se pudo registrar el intento fallido", exc_info=True)


def limpiar(correo, ip):
    """Tras un inicio de sesion correcto se borra la cuenta de ese correo (no la de la IP)."""
    try:
        if correo:
            cache.delete(_clave("correo", correo.strip().lower()))
    except Exception:  # noqa: BLE001
        logger.warning("No se pudo limpiar el freno de inicio de sesion", exc_info=True)


def minutos_de_espera():
    return max(1, VENTANA_SEGUNDOS // 60)
