"""
Etapas iniciales del embudo de ventas (Kanban del CRM).

Una empresa nueva no tenía etapas y el Kanban le pedía crearlas en el admin
de Django, que el dueño no puede usar. Aquí se crean las etapas básicas la
primera vez que se abre el Kanban. Es idempotente: si la empresa ya tiene
cualquier etapa (activa o no), no se toca nada, y la restricción única
(company, name) impide duplicados aunque dos pestañas lleguen a la vez.
"""

from .models import PipelineStage

ETAPAS_INICIALES = (
    ("Nuevo prospecto", 1, False),
    ("Contactado", 2, False),
    ("Cotización enviada", 3, False),
    ("Negociación", 4, False),
    ("Ganado", 5, True),
)


def asegurar_etapas_iniciales(company):
    if company is None:
        return 0
    if PipelineStage.objects.filter(company=company).exists():
        return 0
    creadas = PipelineStage.objects.bulk_create(
        [
            PipelineStage(company=company, name=nombre, order=orden, is_won=ganada)
            for nombre, orden, ganada in ETAPAS_INICIALES
        ],
        ignore_conflicts=True,
    )
    return len(creadas)
