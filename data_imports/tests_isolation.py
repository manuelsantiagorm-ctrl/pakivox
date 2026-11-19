"""
BUG-077: pruebas de aislamiento multiempresa para data_imports.

No cubre el flujo completo de importación (40+ vistas, fuera de
alcance realista hoy). Se enfoca en lo más crítico: que un ImportJob
o sus resultados nunca sean visibles/accesibles desde otra empresa.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase

from companies.models import Company
from data_imports.models import ImportJob, ImportRowResult
from data_imports.views import get_company_job

User = get_user_model()


class ImportJobIsolationTests(TestCase):
    """Verifica que ImportJob respeta el aislamiento multiempresa."""

    def setUp(self):
        self.company_a = Company.objects.create(name="Empresa A")
        self.company_b = Company.objects.create(name="Empresa B")

        self.user_a = User.objects.create_user(
            username="user_a@example.com",
            email="user_a@example.com",
            password="testpass123456",
        )
        self.user_b = User.objects.create_user(
            username="user_b@example.com",
            email="user_b@example.com",
            password="testpass123456",
        )

        self.job_a = ImportJob.objects.create(
            company=self.company_a,
            created_by=self.user_a,
            entity_type=ImportJob.ENTITY_CUSTOMERS,
        )
        self.job_b = ImportJob.objects.create(
            company=self.company_b,
            created_by=self.user_b,
            entity_type=ImportJob.ENTITY_CUSTOMERS,
        )

    def test_company_a_job_not_visible_to_company_b(self):
        """Un ImportJob de la empresa A no debe existir en el queryset de B."""
        jobs_for_b = ImportJob.objects.filter(company=self.company_b)
        self.assertNotIn(self.job_a, jobs_for_b)
        self.assertIn(self.job_b, jobs_for_b)

    def test_get_company_job_blocks_cross_company_access(self):
        """
        get_company_job debe fallar (404) al intentar acceder al job
        de otra empresa, incluso conociendo el ID exacto.
        """
        from django.http import Http404

        with self.assertRaises(Http404):
            get_company_job(company=self.company_b, pk=self.job_a.pk)

        # Confirma que sí funciona con la empresa correcta
        found = get_company_job(company=self.company_a, pk=self.job_a.pk)
        self.assertEqual(found.pk, self.job_a.pk)

    def test_import_row_result_isolated_by_job_company(self):
        """
        ImportRowResult hereda el aislamiento de su ImportJob padre;
        confirma que filtrar por company del job correcto no trae
        filas de un job de otra empresa.
        """
        ImportRowResult.objects.create(
            job=self.job_a,
            row_number=1,
            status=ImportRowResult.STATUS_VALID,
        )
        ImportRowResult.objects.create(
            job=self.job_b,
            row_number=1,
            status=ImportRowResult.STATUS_VALID,
        )

        rows_for_a = ImportRowResult.objects.filter(job__company=self.company_a)
        rows_for_b = ImportRowResult.objects.filter(job__company=self.company_b)

        self.assertEqual(rows_for_a.count(), 1)
        self.assertEqual(rows_for_b.count(), 1)
        self.assertEqual(rows_for_a.first().job_id, self.job_a.pk)
        self.assertEqual(rows_for_b.first().job_id, self.job_b.pk)


class ImportJobFolioTests(TestCase):
    """Verifica que el folio de ImportJob se comporta de forma segura."""

    def setUp(self):
        self.company = Company.objects.create(name="Empresa Folio Test")
        self.user = User.objects.create_user(
            username="user_folio@example.com",
            email="user_folio@example.com",
            password="testpass123456",
        )

    def test_multiple_jobs_can_be_created_same_company(self):
        """
        Crear varios ImportJob seguidos para la misma empresa no debe
        tronar por conflicto de folio/unicidad inesperada.
        """
        jobs = [
            ImportJob.objects.create(
                company=self.company,
                created_by=self.user,
                entity_type=ImportJob.ENTITY_CUSTOMERS,
            )
            for _ in range(3)
        ]
        self.assertEqual(len(jobs), 3)
        self.assertEqual(
            ImportJob.objects.filter(company=self.company).count(), 3
        )
