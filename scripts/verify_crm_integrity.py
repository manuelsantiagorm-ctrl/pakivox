"""
Script de verificación de integridad cross-company en crm/.

Uso:
    python manage.py shell < scripts/verify_crm_integrity.py

Corre ANTES de activar full_clean() automático en save() de Deal,
Activity, CustomerTag, DealNote, DealActivity, DealComment.

Si este script reporta 0 problemas en los 6 bloques, es seguro
activar full_clean() en esos modelos sin romper filas existentes.
Si reporta algo, hay que corregir esos registros primero (o decidir
caso por caso) antes de forzar la validación en save().
"""

from crm.models import (
    Activity,
    CustomerAddress,
    CustomerTag,
    Deal,
    DealActivity,
    DealComment,
    DealNote,
)

print("=" * 70)
print("VERIFICACIÓN DE INTEGRIDAD CROSS-COMPANY — crm/")
print("=" * 70)

total_issues = 0

# 1. Deal.customer / Deal.stage
bad_deal_customer = [
    d for d in Deal.objects.select_related("customer", "company")
    if d.customer_id and d.customer.company_id != d.company_id
]
bad_deal_stage = [
    d for d in Deal.objects.select_related("stage", "company")
    if d.stage_id and d.stage.company_id != d.company_id
]
print(f"\n[Deal] customer de otra empresa: {len(bad_deal_customer)}")
for d in bad_deal_customer[:10]:
    print(f"  - Deal id={d.id} company={d.company_id} customer.company={d.customer.company_id}")
print(f"[Deal] stage de otra empresa: {len(bad_deal_stage)}")
for d in bad_deal_stage[:10]:
    print(f"  - Deal id={d.id} company={d.company_id} stage.company={d.stage.company_id}")
total_issues += len(bad_deal_customer) + len(bad_deal_stage)

# 2. CustomerAddress.customer (ya validado hoy, pero re-verificamos)
bad_addr = [
    a for a in CustomerAddress.objects.select_related("customer")
    if a.customer_id and a.customer.company_id != a.company_id
]
print(f"\n[CustomerAddress] customer de otra empresa: {len(bad_addr)}")
for a in bad_addr[:10]:
    print(f"  - CustomerAddress id={a.id} company={a.company_id} customer.company={a.customer.company_id}")
total_issues += len(bad_addr)

# 3. Activity.customer / Activity.deal
bad_act_cust = [
    a for a in Activity.objects.select_related("customer")
    if a.customer_id and a.customer.company_id != a.company_id
]
bad_act_deal = [
    a for a in Activity.objects.select_related("deal")
    if a.deal_id and a.deal.company_id != a.company_id
]
print(f"\n[Activity] customer de otra empresa: {len(bad_act_cust)}")
print(f"[Activity] deal de otra empresa: {len(bad_act_deal)}")
total_issues += len(bad_act_cust) + len(bad_act_deal)

# 4. CustomerTag — ojo: antes de correr esto, ya debe existir el campo
#    company (tras aplicar la migración de backfill).
try:
    bad_ct_cust = [
        ct for ct in CustomerTag.objects.select_related("customer")
        if ct.customer_id and ct.customer.company_id != ct.company_id
    ]
    bad_ct_tag = [
        ct for ct in CustomerTag.objects.select_related("tag")
        if ct.tag_id and ct.tag.company_id != ct.company_id
    ]
    print(f"\n[CustomerTag] customer de otra empresa: {len(bad_ct_cust)}")
    print(f"[CustomerTag] tag de otra empresa: {len(bad_ct_tag)}")
    total_issues += len(bad_ct_cust) + len(bad_ct_tag)
except Exception as exc:
    print(f"\n[CustomerTag] SALTADO — probablemente falta aplicar la migración de 'company' aún: {exc}")

# 5. DealNote.deal
bad_note = [
    n for n in DealNote.objects.select_related("deal")
    if n.deal_id and n.deal.company_id != n.company_id
]
print(f"\n[DealNote] deal de otra empresa: {len(bad_note)}")
total_issues += len(bad_note)

# 6. DealActivity.deal
bad_dact = [
    d for d in DealActivity.objects.select_related("deal")
    if d.deal_id and d.deal.company_id != d.company_id
]
print(f"\n[DealActivity] deal de otra empresa: {len(bad_dact)}")
total_issues += len(bad_dact)

# 7. DealComment.deal
bad_dcom = [
    d for d in DealComment.objects.select_related("deal")
    if d.deal_id and d.deal.company_id != d.company_id
]
print(f"\n[DealComment] deal de otra empresa: {len(bad_dcom)}")
total_issues += len(bad_dcom)

print("\n" + "=" * 70)
if total_issues == 0:
    print("RESULTADO: 0 problemas encontrados. Seguro activar full_clean().")
else:
    print(f"RESULTADO: {total_issues} problema(s) encontrados. "
          f"Revisar/corregir antes de forzar full_clean() en save().")
print("=" * 70)
