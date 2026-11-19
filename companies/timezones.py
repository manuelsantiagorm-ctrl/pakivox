from datetime import datetime, timezone as datetime_timezone
from zoneinfo import ZoneInfo


def company_zone(company):
    """Devuelve la zona IANA configurada para la empresa."""
    return ZoneInfo(company.timezone)


def company_local_date(company, value):
    """Convierte un instante consciente a la fecha civil de la empresa."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("El instante debe incluir zona horaria.")
    return value.astimezone(company_zone(company)).date()


def company_month_bounds_utc(company, year, month):
    """Periodo mensual local [inicio, fin) expresado en UTC."""
    zone = company_zone(company)
    start_local = datetime(year, month, 1, tzinfo=zone)
    if month == 12:
        end_local = datetime(year + 1, 1, 1, tzinfo=zone)
    else:
        end_local = datetime(year, month + 1, 1, tzinfo=zone)
    return (
        start_local.astimezone(datetime_timezone.utc),
        end_local.astimezone(datetime_timezone.utc),
    )
