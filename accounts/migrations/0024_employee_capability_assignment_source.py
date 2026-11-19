from django.db import migrations, models


SOURCE_JOB = "job"
SOURCE_DIRECT = "direct"


def classify_existing_capability_sources(apps, schema_editor):
    """
    Clasifica la semántica ambigua existente sin cambiar el acceso efectivo.

    - Si una asignación está respaldada por al menos un JobTemplate activo del
      mismo empleado/empresa que concede esa capability, pasa a source="job".
    - En caso contrario conserva source="direct" (default del AddField).

    La clasificación es set-based para PostgreSQL y evita recorrer fila por fila.
    """
    Assignment = apps.get_model("accounts", "EmployeeCapabilityAssignment")
    EmployeeJob = apps.get_model("accounts", "EmployeeJobAssignment")
    JobTemplate = apps.get_model("accounts", "JobTemplate")
    Capability = apps.get_model("accounts", "Capability")
    Through = JobTemplate.capabilities.through

    quote = schema_editor.connection.ops.quote_name

    assignment_table = quote(Assignment._meta.db_table)
    employee_job_table = quote(EmployeeJob._meta.db_table)
    job_table = quote(JobTemplate._meta.db_table)
    capability_table = quote(Capability._meta.db_table)
    through_table = quote(Through._meta.db_table)

    assignment_company = quote(Assignment._meta.get_field("company").column)
    assignment_user = quote(Assignment._meta.get_field("user").column)
    assignment_capability = quote(Assignment._meta.get_field("capability").column)
    assignment_source = quote(Assignment._meta.get_field("source").column)

    employee_job_company = quote(EmployeeJob._meta.get_field("company").column)
    employee_job_user = quote(EmployeeJob._meta.get_field("user").column)
    employee_job_template = quote(
        EmployeeJob._meta.get_field("job_template").column
    )
    employee_job_active = quote(EmployeeJob._meta.get_field("is_active").column)

    job_pk = quote(JobTemplate._meta.pk.column)
    job_active = quote(JobTemplate._meta.get_field("is_active").column)

    capability_pk = quote(Capability._meta.pk.column)
    capability_active = quote(Capability._meta.get_field("is_active").column)

    through_job_field = next(
        field
        for field in Through._meta.fields
        if getattr(getattr(field, "remote_field", None), "model", None) == JobTemplate
    )
    through_capability_field = next(
        field
        for field in Through._meta.fields
        if getattr(getattr(field, "remote_field", None), "model", None) == Capability
    )
    through_job = quote(through_job_field.column)
    through_capability = quote(through_capability_field.column)

    sql = f"""
        UPDATE {assignment_table} AS eca
        SET {assignment_source} = %s
        WHERE EXISTS (
            SELECT 1
            FROM {employee_job_table} AS eja
            INNER JOIN {job_table} AS jt
                ON jt.{job_pk} = eja.{employee_job_template}
            INNER JOIN {through_table} AS jtc
                ON jtc.{through_job} = jt.{job_pk}
            INNER JOIN {capability_table} AS cap
                ON cap.{capability_pk} = jtc.{through_capability}
            WHERE eja.{employee_job_company} = eca.{assignment_company}
              AND eja.{employee_job_user} = eca.{assignment_user}
              AND eja.{employee_job_active} = TRUE
              AND jt.{job_active} = TRUE
              AND cap.{capability_active} = TRUE
              AND jtc.{through_capability} = eca.{assignment_capability}
        )
    """

    with schema_editor.connection.cursor() as cursor:
        cursor.execute(sql, [SOURCE_JOB])


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0023_email1_verification_code"),
    ]

    operations = [
        migrations.AddField(
            model_name="employeecapabilityassignment",
            name="source",
            field=models.CharField(
                choices=[
                    (SOURCE_JOB, "Derivada de puesto"),
                    (SOURCE_DIRECT, "Asignación directa"),
                ],
                default=SOURCE_DIRECT,
                max_length=12,
            ),
        ),
        migrations.RunPython(
            classify_existing_capability_sources,
            migrations.RunPython.noop,
        ),
    ]
