"""
IP real del visitante detrás de nginx.

En producción gunicorn solo recibe conexiones de nginx (127.0.0.1 o socket
unix), así que REMOTE_ADDR es siempre la del proxy: usarla tal cual metía a
TODOS los usuarios en el mismo contador del freno de inicio de sesión (20
fallos de cualquiera bloqueaban el acceso de todos).

Regla: solo si la conexión viene de un proxy local o privado se lee
X-Forwarded-For, y de ahí solo el ÚLTIMO valor (el que agregó nginx); los de
la izquierda los puede inventar el cliente. Una conexión directa desde
internet usa REMOTE_ADDR y nunca confía en encabezados.
"""

import ipaddress


def _parse(value):
    value = (value or "").strip()
    if not value:
        return None
    # "1.2.3.4:5678" o "[::1]:5678"
    if value.startswith("[") and "]" in value:
        value = value[1 : value.index("]")]
    elif value.count(":") == 1:
        value = value.split(":", 1)[0]
    try:
        return ipaddress.ip_address(value)
    except ValueError:
        return None


def _is_proxy(ip):
    return ip is None or ip.is_loopback or ip.is_private or ip.is_link_local


def get_client_ip(request):
    meta = request.META
    remote_raw = meta.get("REMOTE_ADDR", "")
    remote = _parse(remote_raw)

    if not _is_proxy(remote):
        return str(remote)

    forwarded = meta.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        candidate = _parse(forwarded.split(",")[-1])
        if candidate is not None:
            return str(candidate)

    real_ip = _parse(meta.get("HTTP_X_REAL_IP", ""))
    if real_ip is not None:
        return str(real_ip)

    if remote is None or remote.is_loopback:
        # Sin encabezado del proxy no hay forma de distinguir visitantes: mejor
        # no contar por IP que bloquear a todos juntos.
        return None

    return str(remote)
