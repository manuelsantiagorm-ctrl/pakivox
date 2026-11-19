from pathlib import Path

path = Path("dashboard/views.py")
text = path.read_text(encoding="utf-8")

start_marker = "    # ==========================================================\n    # FUNNEL CRM"
end_marker = "    # Top SKUs vendidos"

start = text.find(start_marker)
end = text.find(end_marker)

if start == -1:
    raise SystemExit("No encontré el inicio del bloque FUNNEL CRM.")

if end == -1:
    raise SystemExit("No encontré el marcador '# Top SKUs vendidos'.")

new_block = '''    # ==========================================================
    # FUNNEL CRM
    # Pipeline comercial tradicional por etapas CRM.
    # ==========================================================
    stages = PipelineStage.objects.filter(
        is_active=True,
    ).order_by(
        "order",
    )

    if company:
        stages = stages.filter(company=company)

    funnel = []

    for stage in stages:
        total = deals_qs.filter(stage=stage).count()

        funnel.append(
            {
                "name": stage.name,
                "count": total,
            }
        )

'''

fixed_text = text[:start] + new_block + text[end:]

path.write_text(fixed_text, encoding="utf-8")

print("Bloque FUNNEL CRM corregido correctamente.")