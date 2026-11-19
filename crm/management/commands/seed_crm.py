from decimal import Decimal
import random

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand

from companies.models import Company
from crm.models import Customer, PipelineStage, Deal, Activity


class Command(BaseCommand):
    help = "Crea datos demo para CRM: etapas, clientes, oportunidades y actividades."

    def handle(self, *args, **options):
        company, _ = Company.objects.get_or_create(
            slug="demo-mayorista",
            defaults={
                "name": "Demo Mayorista",
                "rfc": "DEM010101XXX",
                "email": "demo@wms360.com",
                "phone": "5555555555",
                "address": "Ciudad de México",
            },
        )

        user = User.objects.filter(is_superuser=True).first()

        stages_data = [
            ("Nuevo Lead", 1, False, False),
            ("Contactado", 2, False, False),
            ("Cotización enviada", 3, False, False),
            ("Negociación", 4, False, False),
            ("Pago pendiente", 5, False, False),
            ("Cliente activo", 6, False, False),
            ("Ganado", 7, True, False),
            ("Perdido", 8, False, True),
        ]

        stages = []
        for name, order, is_won, is_lost in stages_data:
            stage, _ = PipelineStage.objects.get_or_create(
                company=company,
                name=name,
                defaults={
                    "order": order,
                    "is_won": is_won,
                    "is_lost": is_lost,
                    "is_active": True,
                },
            )
            stages.append(stage)

        names = [
            "Distribuidora El Norte",
            "Mayorista Central MX",
            "Comercializadora Rivera",
            "Importaciones León",
            "Ferretería San Ángel",
            "Cosméticos Luna",
            "Electrónicos Delta",
            "Abarrotera La Unión",
            "Grupo Textil Roma",
            "Refacciones del Bajío",
            "Suministros Express",
            "Distribuciones Plata",
            "Comercial Maya",
            "Mayorista Querétaro",
            "Importadora Pacífico",
        ]

        sources = [
            "whatsapp",
            "facebook",
            "instagram",
            "tiktok",
            "website",
            "referral",
            "marketplace",
            "manual",
        ]
        customer_types = ["lead", "prospect", "customer", "vip", "distributor"]

        customers = []
        for i, name in enumerate(names, start=1):
            customer, _ = Customer.objects.get_or_create(
                company=company,
                full_name=name,
                defaults={
                    "business_name": name,
                    "rfc": f"RFC{i:03d}0101XX{i}",
                    "email": f"cliente{i}@demo.com",
                    "phone": f"555000{i:04d}",
                    "whatsapp": f"555100{i:04d}",
                    "customer_type": random.choice(customer_types),
                    "source": random.choice(sources),
                    "assigned_to": user,
                    "estimated_monthly_purchase": Decimal(
                        random.randint(20000, 300000)
                    ),
                    "notes": "Cliente demo generado automáticamente para probar WMS360.",
                    "is_active": True,
                },
            )
            customers.append(customer)

        for i in range(1, 26):
            customer = random.choice(customers)
            stage = random.choice(stages[:-1])

            Deal.objects.get_or_create(
                company=company,
                customer=customer,
                title=f"Oportunidad mayorista #{i}",
                defaults={
                    "stage": stage,
                    "assigned_to": user,
                    "amount": Decimal(random.randint(15000, 250000)),
                    "probability": random.choice([10, 20, 30, 40, 50, 70, 80]),
                    "status": "open",
                    "notes": "Oportunidad demo creada para visualizar el Kanban CRM.",
                },
            )

        activity_types = [
            "call",
            "whatsapp",
            "email",
            "meeting",
            "note",
            "task",
            "quote",
            "follow_up",
        ]

        for i in range(1, 31):
            customer = random.choice(customers)
            Activity.objects.get_or_create(
                company=company,
                customer=customer,
                title=f"Actividad comercial #{i}",
                defaults={
                    "assigned_to": user,
                    "activity_type": random.choice(activity_types),
                    "description": "Actividad demo para seguimiento comercial.",
                },
            )

        self.stdout.write(self.style.SUCCESS("CRM demo creado correctamente."))
