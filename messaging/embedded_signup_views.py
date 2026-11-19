"""
WMS360 · WhatsApp Meta Embedded Signup · ES-2 Hardened

Objetivo de esta fase:
- validar server-side la autorización de Meta;
- validar Business propietario, WABA y Phone Number;
- persistir la configuración como NO operativa;
- dejar preparado ES-3 sin crear/activar WhatsAppNumber ni suscribir webhooks.

Reglas de producción:
- company sale exclusivamente de la sesión;
- empleado/profile debe estar activo;
- bodega se revalida con ACCESS_MANAGE + company + is_active;
- todos los IDs del navegador son candidatos no confiables;
- authorization code se consume una sola vez mediante estado server-side;
- no se hacen retries automáticos del canje del code;
- no se exponen ni registran tokens, App Secret o Authorization;
- el lock se toma sobre la fila base Company, sin joins opcionales;
- configuración permanece is_active=False hasta ES-3.
"""

import hmac
import json
import logging
import secrets
import time

import requests

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET, require_POST

from accounts.warehouse_access import (
    ACCESS_MANAGE,
    get_accessible_warehouses,
    get_user_company,
)
from subscriptions.access import whatsapp_premium_required

from .meta_embedded_signup import (
    MetaEmbeddedSignupConfigurationError,
    get_embedded_signup_settings,
    get_public_embedded_signup_context,
)
from .models import WhatsAppConfig
from .meta_provisioning import MetaProvisioningError, provision_meta_channel


logger = logging.getLogger(__name__)

META_GRAPH_BASE_URL = "https://graph.facebook.com"
META_CONNECT_TIMEOUT_SECONDS = 5
META_READ_TIMEOUT_SECONDS = 20
MAX_AUTHORIZATION_CODE_LENGTH = 4096
MAX_META_ID_LENGTH = 120
SIGNUP_NONCE_TTL_SECONDS = 10 * 60
PHONE_PAGE_SIZE = 100
PHONE_MAX_PAGES = 10


class MetaEmbeddedSignupRemoteError(Exception):
    """Error seguro para exponer al cliente sin filtrar secretos."""

    def __init__(self, message, *, status=502, meta_code=None):
        super().__init__(message)
        self.public_message = str(message)
        self.status = int(status)
        self.meta_code = (
            str(meta_code)[:30]
            if meta_code not in (None, "")
            else None
        )


def _json_error(message, *, status, meta_code=None):
    response = JsonResponse(
        {"ok": False, "error": str(message)},
        status=int(status),
    )
    if meta_code:
        # Solo código numérico/clasificador seguro, nunca body ni token.
        response["X-WMS360-Meta-Code"] = str(meta_code)[:30]
    return response


def _get_active_employee_profile(user):
    """
    Contrato explícito de empleado activo.

    WMS360 ya usa request.user.profile como fuente de company.
    Esta fase no acepta perfiles inexistentes o inactivos.
    """
    profile = getattr(user, "profile", None)
    if profile is None:
        return None

    if getattr(profile, "is_active", False) is not True:
        return None

    if getattr(profile, "company_id", None) is None:
        return None

    return profile


def _get_manageable_warehouses(*, user, company):
    """
    Siempre limita por:
    - usuario;
    - nivel manage;
    - company actual;
    - bodega activa.
    """
    return (
        get_accessible_warehouses(
            user=user,
            required_level=ACCESS_MANAGE,
        )
        .filter(
            company=company,
            is_active=True,
        )
        .order_by("name", "id")
    )


def _safe_meta_error_code(response):
    try:
        payload = response.json()
    except Exception:
        return None

    if not isinstance(payload, dict):
        return None

    error = payload.get("error")
    if not isinstance(error, dict):
        return None

    code = error.get("code")
    if code is None:
        return None

    normalized = str(code).strip()
    if not normalized:
        return None

    return normalized[:30]


def _normalize_meta_id(value, *, required=True):
    normalized = str(value or "").strip()

    if not normalized:
        if required:
            raise MetaEmbeddedSignupRemoteError(
                "Meta no devolvió todos los identificadores necesarios.",
                status=400,
            )
        return ""

    if (
        len(normalized) > MAX_META_ID_LENGTH
        or not normalized.isdigit()
    ):
        raise MetaEmbeddedSignupRemoteError(
            "Meta devolvió un identificador técnico inválido.",
            status=400,
        )

    return normalized


def _parse_meta_response(response, *, public_error):
    """
    HTTP 200 no es suficiente:
    - exige JSON;
    - exige dict;
    - en error extrae solo código seguro.
    """
    try:
        payload = response.json()
    except Exception as error:
        raise MetaEmbeddedSignupRemoteError(
            "Meta devolvió una respuesta que no contiene JSON válido.",
            status=502,
        ) from error

    if not response.ok:
        raise MetaEmbeddedSignupRemoteError(
            public_error,
            status=400,
            meta_code=_safe_meta_error_code(response),
        )

    if not isinstance(payload, dict):
        raise MetaEmbeddedSignupRemoteError(
            "Meta devolvió una respuesta inesperada.",
            status=502,
        )

    return payload


def _meta_get(*, api_version, object_path, access_token, params=None):
    endpoint = (
        f"{META_GRAPH_BASE_URL}/"
        f"{api_version}/"
        f"{str(object_path).lstrip('/')}"
    )

    try:
        response = requests.get(
            endpoint,
            headers={"Authorization": f"Bearer {access_token}"},
            params=params or {},
            timeout=(
                META_CONNECT_TIMEOUT_SECONDS,
                META_READ_TIMEOUT_SECONDS,
            ),
        )
    except requests.Timeout as error:
        raise MetaEmbeddedSignupRemoteError(
            "Meta no respondió dentro del tiempo permitido.",
            status=502,
        ) from error
    except requests.ConnectionError as error:
        raise MetaEmbeddedSignupRemoteError(
            "No fue posible establecer conexión con Meta.",
            status=502,
        ) from error
    except requests.RequestException as error:
        raise MetaEmbeddedSignupRemoteError(
            "Ocurrió un error HTTP comunicándose con Meta.",
            status=502,
        ) from error

    return _parse_meta_response(
        response,
        public_error="Meta rechazó la consulta de los activos autorizados.",
    )


def _exchange_authorization_code(*, meta_configuration, authorization_code):
    """
    No reintenta automáticamente.

    El authorization code es de un solo uso; ante resultado incierto
    el usuario debe iniciar un Embedded Signup nuevo.
    """
    token_endpoint = (
        f"{META_GRAPH_BASE_URL}/"
        f"{meta_configuration.api_version}/oauth/access_token"
    )

    try:
        token_response = requests.get(
            token_endpoint,
            params={
                "client_id": meta_configuration.app_id,
                "client_secret": meta_configuration.app_secret,
                "code": authorization_code,
            },
            timeout=(
                META_CONNECT_TIMEOUT_SECONDS,
                META_READ_TIMEOUT_SECONDS,
            ),
        )
    except requests.Timeout as error:
        raise MetaEmbeddedSignupRemoteError(
            "Meta no confirmó el canje del código. "
            "Inicia una conexión nueva; WMS360 no repetirá "
            "automáticamente esta operación.",
            status=502,
        ) from error
    except requests.ConnectionError as error:
        raise MetaEmbeddedSignupRemoteError(
            "La conexión con Meta se interrumpió durante la autorización. "
            "Inicia una conexión nueva.",
            status=502,
        ) from error
    except requests.RequestException as error:
        raise MetaEmbeddedSignupRemoteError(
            "Ocurrió un error HTTP durante la autorización con Meta.",
            status=502,
        ) from error

    token_payload = _parse_meta_response(
        token_response,
        public_error=(
            "Meta no pudo completar la autorización. "
            "Recarga la página e inténtalo nuevamente."
        ),
    )

    business_access_token = str(
        token_payload.get("access_token") or ""
    ).strip()

    if (
        not business_access_token
        or len(business_access_token) < 40
        or any(
            character.isspace()
            for character in business_access_token
        )
    ):
        raise MetaEmbeddedSignupRemoteError(
            "Meta no devolvió una credencial válida.",
            status=502,
        )

    return business_access_token


def _validate_waba_owner(
    *,
    meta_configuration,
    business_access_token,
    candidate_waba_id,
    candidate_business_id,
):
    """
    WABA y Business ID recibidos del navegador son candidatos.

    Graph API es la fuente autoritativa.
    """
    waba_payload = _meta_get(
        api_version=meta_configuration.api_version,
        object_path=candidate_waba_id,
        access_token=business_access_token,
        params={"fields": "id,name,owner_business_info"},
    )

    authoritative_waba_id = _normalize_meta_id(
        waba_payload.get("id")
    )

    if not hmac.compare_digest(
        authoritative_waba_id,
        candidate_waba_id,
    ):
        raise MetaEmbeddedSignupRemoteError(
            "Meta devolvió una WABA distinta a la seleccionada.",
            status=403,
        )

    owner_business_info = waba_payload.get("owner_business_info")
    if not isinstance(owner_business_info, dict):
        raise MetaEmbeddedSignupRemoteError(
            "Meta no permitió validar el negocio propietario de la WABA.",
            status=502,
        )

    authoritative_business_id = _normalize_meta_id(
        owner_business_info.get("id")
    )

    if (
        candidate_business_id
        and not hmac.compare_digest(
            authoritative_business_id,
            candidate_business_id,
        )
    ):
        raise MetaEmbeddedSignupRemoteError(
            "El negocio devuelto por Meta no coincide con "
            "el propietario de la WABA.",
            status=403,
        )

    return {
        "business_id": authoritative_business_id,
        "business_name": str(
            owner_business_info.get("name") or ""
        ).strip()[:200],
        "waba_id": authoritative_waba_id,
        "waba_name": str(
            waba_payload.get("name") or ""
        ).strip()[:200],
    }


def _validate_phone_in_waba(
    *,
    meta_configuration,
    business_access_token,
    waba_id,
    candidate_phone_number_id,
):
    """
    Confirma membership Phone Number -> WABA.

    Si candidate_phone_number_id llega vacío (caso típico de
    Coexistence, donde Meta no lo entrega en el evento FINISH del
    navegador), se intenta auto-detectar: si la WABA tiene
    exactamente un número registrado, se usa ese. Si tiene más
    de uno, se exige selección explícita para no adivinar.

    Paginación acotada para evitar respuestas enormes o loops.
    """
    after = ""
    collected_rows = []

    for _page_number in range(PHONE_MAX_PAGES):
        params = {
            "fields": ("id,display_phone_number," "verified_name,quality_rating"),
            "limit": PHONE_PAGE_SIZE,
        }

        if after:
            params["after"] = after

        payload = _meta_get(
            api_version=meta_configuration.api_version,
            object_path=f"{waba_id}/phone_numbers",
            access_token=business_access_token,
            params=params,
        )

        rows = payload.get("data")
        if not isinstance(rows, list):
            raise MetaEmbeddedSignupRemoteError(
                "Meta devolvió una lista de números inválida.",
                status=502,
            )

        for row in rows:
            if not isinstance(row, dict):
                continue

            row_id = str(row.get("id") or "").strip()
            if not row_id.isdigit():
                continue

            if candidate_phone_number_id:
                if not hmac.compare_digest(
                    row_id,
                    candidate_phone_number_id,
                ):
                    continue

                return _build_phone_result(row)

            collected_rows.append(row)

        paging = payload.get("paging")
        if not isinstance(paging, dict):
            break

        cursors = paging.get("cursors")
        if not isinstance(cursors, dict):
            break

        next_after = str(cursors.get("after") or "").strip()

        if not next_after or hmac.compare_digest(next_after, after):
            break

        after = next_after

    if candidate_phone_number_id:
        raise MetaEmbeddedSignupRemoteError(
            "El número seleccionado no pertenece a la WABA autorizada.",
            status=403,
        )

    if len(collected_rows) == 1:
        return _build_phone_result(collected_rows[0])

    if len(collected_rows) == 0:
        raise MetaEmbeddedSignupRemoteError(
            "Meta no reportó ningún número registrado en esta WABA. "
            "Verifica el número desde WhatsApp Manager e inténtalo "
            "nuevamente.",
            status=409,
        )

    raise MetaEmbeddedSignupRemoteError(
        "Esta WABA tiene más de un número registrado y Meta no "
        "especificó cuál conectar. Contacta a soporte de WMS360 "
        "para completar la selección manual.",
        status=409,
    )


def _build_phone_result(row):
    display_number = str(row.get("display_phone_number") or "").strip()

    if not display_number:
        raise MetaEmbeddedSignupRemoteError(
            "Meta encontró el número, pero no devolvió " "su número visible.",
            status=502,
        )

    row_id = str(row.get("id") or "").strip()

    return {
        "phone_number_id": row_id,
        "display_number": display_number[:40],
        "verified_name": str(row.get("verified_name") or "").strip()[:200],
        "quality_rating": str(row.get("quality_rating") or "").strip()[:30],
    }


def _consume_signup_state(request):
    """
    Consume el estado antes del canje del code.

    Si luego hay timeout o resultado incierto, el mismo intento
    no puede repetirse con el mismo nonce.
    """
    state = request.session.pop(
        "meta_embedded_signup_state",
        None,
    )

    # Compatibilidad transitoria con el ES-1 ya instalado.
    legacy_nonce = request.session.pop(
        "meta_embedded_signup_nonce",
        "",
    )

    request.session.modified = True

    if isinstance(state, dict):
        return state

    if legacy_nonce:
        return {
            "nonce": str(legacy_nonce),
            "issued_at": None,
            "user_id": request.user.pk,
            "company_id": None,
            "legacy": True,
        }

    return None


@login_required(login_url="/accounts/login/")
@whatsapp_premium_required
@require_GET
def meta_embedded_signup_start(request):
    profile = _get_active_employee_profile(request.user)

    if profile is None:
        raise PermissionDenied(
            "Tu usuario no tiene un empleado activo asociado."
        )

    company = get_user_company(request.user)

    if (
        company is None
        or company.pk != profile.company_id
    ):
        raise PermissionDenied(
            "No se detectó una empresa activa válida para el usuario."
        )

    warehouses = _get_manageable_warehouses(
        user=request.user,
        company=company,
    )

    if not warehouses.exists():
        raise PermissionDenied(
            "Necesitas acceso de administración "
            "a una bodega activa para conectar WhatsApp."
        )

    try:
        public_configuration = get_public_embedded_signup_context()
    except MetaEmbeddedSignupConfigurationError:
        raise PermissionDenied(
            "Embedded Signup de Meta no está habilitado correctamente."
        )

    signup_nonce = secrets.token_urlsafe(32)

    request.session["meta_embedded_signup_state"] = {
        "nonce": signup_nonce,
        "issued_at": int(time.time()),
        "user_id": request.user.pk,
        "company_id": company.pk,
    }
    request.session.modified = True

    existing_configuration = (
        WhatsAppConfig.objects
        .filter(
            company=company,
            is_active=True,
        )
        .only(
            "id",
            "waba_id",
            "updated_at",
        )
        .first()
    )
    response = render(
        request,
        "messaging/meta_embedded_signup_start.html",
        {
            "company": company,
            "warehouses": warehouses,
            "meta_public": public_configuration,
            "signup_nonce": signup_nonce,
            "embedded_signup_ready": True,
            "existing_configuration": existing_configuration,
        },
    )
    # Meta Embedded Signup abre un popup que necesita comunicarse
    # de vuelta con esta ventana mediante postMessage. El valor
    # por defecto de Django ("same-origin") aísla la ventana y
    # rompe esa comunicación. Solo esta vista puntual necesita
    # relajar la política; el resto del sitio conserva
    # same-origin sin cambios.
    response["Cross-Origin-Opener-Policy"] = (
        "same-origin-allow-popups"
    )
    return response


@login_required(login_url="/accounts/login/")
@whatsapp_premium_required
@require_POST
def meta_embedded_signup_callback(request):
    profile = _get_active_employee_profile(request.user)

    if profile is None:
        return _json_error(
            "Tu usuario no tiene un empleado activo asociado.",
            status=403,
        )

    company = get_user_company(request.user)

    if (
        company is None
        or company.pk != profile.company_id
    ):
        return _json_error(
            "No se detectó una empresa activa válida.",
            status=403,
        )

    try:
        payload = json.loads(
            request.body.decode("utf-8") or "{}"
        )
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
    ):
        return _json_error(
            "La solicitud recibida no es válida.",
            status=400,
        )

    if not isinstance(payload, dict):
        return _json_error(
            "La solicitud recibida no es válida.",
            status=400,
        )

    authorization_code = str(
        payload.get("code") or ""
    ).strip()
    received_nonce = str(
        payload.get("nonce") or ""
    ).strip()
    warehouse_id_raw = str(
        payload.get("warehouse_id") or ""
    ).strip()

    try:
        candidate_business_id = _normalize_meta_id(
            payload.get("business_id"),
            required=False,
        )
        candidate_waba_id = _normalize_meta_id(
            payload.get("waba_id")
        )
        candidate_phone_number_id = _normalize_meta_id(
            payload.get("phone_number_id"),
            required=False,
        )
    except MetaEmbeddedSignupRemoteError as error:
        return _json_error(
            error.public_message,
            status=error.status,
        )

    state = _consume_signup_state(request)

    if not isinstance(state, dict):
        return _json_error(
            "La sesión de conexión expiró. "
            "Recarga la página e inténtalo nuevamente.",
            status=403,
        )

    expected_nonce = str(
        state.get("nonce") or ""
    ).strip()

    is_legacy_state = bool(
        state.get("legacy")
    )
    state_is_fresh = True

    if not is_legacy_state:
        issued_at = state.get("issued_at")
        now_epoch = int(time.time())
        state_is_fresh = (
            isinstance(issued_at, int)
            and 0 <= (
                now_epoch - issued_at
            ) <= SIGNUP_NONCE_TTL_SECONDS
        )

    if (
        not expected_nonce
        or not received_nonce
        or not hmac.compare_digest(
            expected_nonce,
            received_nonce,
        )
        or state.get("user_id") != request.user.pk
        or (
            state.get("company_id") is not None
            and state.get("company_id") != company.pk
        )
        or not state_is_fresh
    ):
        return _json_error(
            "La sesión de conexión expiró. "
            "Recarga la página e inténtalo nuevamente.",
            status=403,
        )

    if (
        not authorization_code
        or len(authorization_code) > MAX_AUTHORIZATION_CODE_LENGTH
        or any(
            character.isspace()
            for character in authorization_code
        )
    ):
        return _json_error(
            "Meta no devolvió un código de autorización válido.",
            status=400,
        )

    if (
        not warehouse_id_raw.isdigit()
        or int(warehouse_id_raw) <= 0
    ):
        return _json_error(
            "Selecciona una bodega válida.",
            status=400,
        )

    warehouse = (
        _get_manageable_warehouses(
            user=request.user,
            company=company,
        )
        .filter(
            pk=int(warehouse_id_raw),
        )
        .first()
    )

    if warehouse is None:
        return _json_error(
            "No tienes autorización para administrar "
            "la bodega seleccionada.",
            status=403,
        )

    try:
        meta_configuration = get_embedded_signup_settings()
    except MetaEmbeddedSignupConfigurationError:
        return _json_error(
            "La conexión con Meta no está disponible temporalmente.",
            status=503,
        )

    try:
        business_access_token = _exchange_authorization_code(
            meta_configuration=meta_configuration,
            authorization_code=authorization_code,
        )

        validated_waba = _validate_waba_owner(
            meta_configuration=meta_configuration,
            business_access_token=business_access_token,
            candidate_waba_id=candidate_waba_id,
            candidate_business_id=candidate_business_id,
        )

        validated_phone = _validate_phone_in_waba(
            meta_configuration=meta_configuration,
            business_access_token=business_access_token,
            waba_id=validated_waba["waba_id"],
            candidate_phone_number_id=(
                candidate_phone_number_id
            ),
        )

    except MetaEmbeddedSignupRemoteError as error:
        logger.warning(
            "Meta Embedded Signup ES-2 rechazado "
            "company_id=%s user_id=%s warehouse_id=%s "
            "meta_code=%s",
            company.pk,
            request.user.pk,
            warehouse.pk,
            error.meta_code or "",
        )

        return _json_error(
            error.public_message,
            status=error.status,
            meta_code=error.meta_code,
        )

    with transaction.atomic():
        # Regla PostgreSQL: bloquear primero la fila base Company.
        # No hay select_for_update() sobre relaciones opcionales.
        type(company).objects.select_for_update().only(
            "pk"
        ).get(
            pk=company.pk,
        )

        configuration, created = (
            WhatsAppConfig.objects.get_or_create(
                company=company,
                defaults={
                    "is_active": False,
                },
            )
        )

        current_waba_id = str(
            configuration.waba_id or ""
        ).strip()

        if (
            not created
            and configuration.is_active
            and current_waba_id
            and not hmac.compare_digest(
                current_waba_id,
                validated_waba["waba_id"],
            )
        ):
            return _json_error(
                "Esta empresa ya tiene otra WABA activa. "
                "Para sustituirla se requiere el flujo "
                "seguro de desconexión/reconexión.",
                status=409,
            )

        configuration.meta_business_id = (
            validated_waba["business_id"]
        )
        configuration.waba_id = (
            validated_waba["waba_id"]
        )
        configuration.permanent_access_token = (
            business_access_token
        )

        # ES-2: validado, todavía NO operativo.
        configuration.is_active = False

        configuration.save(
            update_fields=[
                "meta_business_id",
                "waba_id",
                "permanent_access_token",
                "is_active",
                "updated_at",
            ]
        )

    request.session["meta_embedded_signup_pending"] = {
        "company_id": company.pk,
        "warehouse_id": warehouse.pk,
        "configuration_id": configuration.pk,
        "business_id": validated_waba["business_id"],
        "waba_id": validated_waba["waba_id"],
        "phone_number_id": (
            validated_phone["phone_number_id"]
        ),
        "display_number": (
            validated_phone["display_number"]
        ),
        "verified_name": (
            validated_phone["verified_name"]
        ),
    }
    request.session.modified = True

    try:
        provisioning = provision_meta_channel(
            company=company,
            warehouse=warehouse,
            configuration=configuration,
            phone_number_id=validated_phone["phone_number_id"],
            display_number=validated_phone["display_number"],
            verified_name=validated_phone["verified_name"],
        )
    except MetaProvisioningError as error:
        logger.warning(
            "Meta Embedded Signup ES-3 provisioning rejected "
            "company_id=%s user_id=%s warehouse_id=%s code=%s",
            company.pk,
            request.user.pk,
            warehouse.pk,
            error.code,
        )

        return _json_error(
            error.public_message,
            status=error.status,
        )

    if provisioning.activated:
        request.session.pop(
            "meta_embedded_signup_pending",
            None,
        )
        request.session.modified = True

        return JsonResponse(
            {
                "ok": True,
                "message": (
                    "WhatsApp quedó conectado y validado "
                    "correctamente en WMS360."
                ),
                "next_step": "ready",
                "channel_id": provisioning.channel_id,
                "status": provisioning.status,
                "phone": {
                    "display_number": provisioning.display_number,
                    "verified_name": provisioning.verified_name,
                    "quality_rating": validated_phone["quality_rating"],
                },
            },
            status=200,
        )

    return JsonResponse(
        {
            "ok": True,
            "message": (
                "La activación ya está siendo procesada. "
                "WMS360 no inició un segundo aprovisionamiento."
            ),
            "next_step": "verifying",
            "channel_id": provisioning.channel_id,
            "status": provisioning.status,
            "phone": {
                "display_number": provisioning.display_number,
                "verified_name": provisioning.verified_name,
                "quality_rating": validated_phone["quality_rating"],
            },
        },
        status=202,
    )
