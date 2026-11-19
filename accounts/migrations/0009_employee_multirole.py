from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def migrate_existing_assignments(apps, schema_editor):
    Invitation = apps.get_model("accounts", "EmployeeInvitation")
    InvitationJob = apps.get_model("accounts", "InvitationJobAssignment")
    EmployeeJob = apps.get_model("accounts", "EmployeeJobAssignment")
    EmployeeCapability = apps.get_model("accounts", "EmployeeCapabilityAssignment")
    JobTemplate = apps.get_model("accounts", "JobTemplate")
    UserProfile = apps.get_model("accounts", "UserProfile")

    for invitation in Invitation.objects.exclude(job_template_id=None).iterator():
        InvitationJob.objects.get_or_create(
            invitation_id=invitation.pk,
            job_template_id=invitation.job_template_id,
            defaults={"is_primary": True},
        )
        if invitation.accepted_user_id:
            EmployeeJob.objects.get_or_create(
                company_id=invitation.company_id,
                user_id=invitation.accepted_user_id,
                job_template_id=invitation.job_template_id,
                defaults={
                    "assigned_by_id": invitation.invited_by_id,
                    "is_primary": True,
                    "is_active": True,
                },
            )

    for profile in UserProfile.objects.exclude(role__in=["owner", "admin"]).iterator():
        if EmployeeJob.objects.filter(
            company_id=profile.company_id, user_id=profile.user_id, is_active=True
        ).exists():
            continue
        capability_codes = set(
            EmployeeCapability.objects.filter(
                company_id=profile.company_id,
                user_id=profile.user_id,
                is_active=True,
            ).values_list("capability__code", flat=True)
        )
        candidates = []
        for template in JobTemplate.objects.filter(
            company_id=profile.company_id, is_active=True
        ).prefetch_related("capabilities"):
            template_codes = set(
                template.capabilities.filter(is_active=True).values_list(
                    "code", flat=True
                )
            )
            overlap = len(template_codes & capability_codes)
            if overlap:
                candidates.append((overlap, -len(template_codes), template.pk))
        if candidates:
            template_id = max(candidates)[2]
            EmployeeJob.objects.create(
                company_id=profile.company_id,
                user_id=profile.user_id,
                job_template_id=template_id,
                is_primary=True,
                is_active=True,
            )


class Migration(migrations.Migration):
    dependencies = [("accounts", "0008_eight_work_areas")]

    operations = [
        migrations.CreateModel(
            name="EmployeeAccessAudit",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("action", models.CharField(max_length=40)),
                ("detail", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("actor", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to=settings.AUTH_USER_MODEL)),
                ("company", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="companies.company")),
                ("employee", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="+", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.CreateModel(
            name="EmployeeJobAssignment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("is_primary", models.BooleanField(default=False)),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("assigned_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to=settings.AUTH_USER_MODEL)),
                ("company", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="employee_job_assignments", to="companies.company")),
                ("job_template", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="employee_assignments", to="accounts.jobtemplate")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="job_assignments", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-is_primary", "job_template__name"]},
        ),
        migrations.CreateModel(
            name="InvitationJobAssignment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("is_primary", models.BooleanField(default=False)),
                ("invitation", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="job_assignments", to="accounts.employeeinvitation")),
                ("job_template", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to="accounts.jobtemplate")),
            ],
            options={"ordering": ["-is_primary", "job_template__name"]},
        ),
        migrations.AddIndex(
            model_name="employeeaccessaudit",
            index=models.Index(fields=["company", "employee", "-created_at"], name="emp_access_audit_idx"),
        ),
        migrations.AddConstraint(
            model_name="employeejobassignment",
            constraint=models.UniqueConstraint(fields=("user", "job_template"), name="unique_employee_job_assignment"),
        ),
        migrations.AddConstraint(
            model_name="employeejobassignment",
            constraint=models.UniqueConstraint(condition=models.Q(("is_active", True), ("is_primary", True)), fields=("user",), name="unique_active_primary_job_per_employee"),
        ),
        migrations.AddConstraint(
            model_name="invitationjobassignment",
            constraint=models.UniqueConstraint(fields=("invitation", "job_template"), name="unique_invitation_job_assignment"),
        ),
        migrations.AddConstraint(
            model_name="invitationjobassignment",
            constraint=models.UniqueConstraint(condition=models.Q(("is_primary", True)), fields=("invitation",), name="unique_primary_job_per_invitation"),
        ),
        migrations.RunPython(migrate_existing_assignments, migrations.RunPython.noop),
    ]
