from django.contrib.auth import logout


SESSION_ACCESS_VERSION = "wms360_access_version"


def _perfil_sin_acceso(user, profile):
    """Un trabajador dado de baja, suspendido o desactivado no conserva sesión.

    El superusuario de plataforma no depende de un perfil de empresa.
    """
    if getattr(user, "is_superuser", False):
        return False
    if not profile.is_active:
        return True
    return profile.employment_status != profile.STATUS_ACTIVE


class AccessVersionMiddleware:
    """Invalida una sesión abierta cuando cambian accesos sensibles.

    También cierra la sesión de quien ya no tiene un perfil activo (baja,
    suspensión o perfil desactivado): así ninguna pantalla que solo pida
    "estar logueado" queda al alcance de un ex trabajador.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user and user.is_authenticated:
            profile = getattr(user, "profile", None)
            if profile is not None:
                current = int(profile.access_version)
                known = request.session.get(SESSION_ACCESS_VERSION)
                if _perfil_sin_acceso(user, profile):
                    logout(request)
                elif known is None:
                    request.session[SESSION_ACCESS_VERSION] = current
                elif int(known) != current:
                    logout(request)
        return self.get_response(request)
