from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("applications", "0006_unified_service_type")]

    operations = [
        migrations.RemoveConstraint("formfield", "form_field_requirement"),
        migrations.RemoveConstraint("serviceoption", "service_opt_out_free"),
        migrations.RemoveConstraint("serviceoption", "service_one_opt_out"),
        migrations.RemoveField("formfield", "is_recommended"),
        migrations.RemoveField("serviceoption", "is_opt_out"),
    ]
