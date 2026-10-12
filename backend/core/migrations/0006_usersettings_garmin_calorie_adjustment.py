from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0005_dailylog_fiber_g_dailylog_salt_g_dailylog_sugar_g"),
    ]

    operations = [
        migrations.AddField(
            model_name="usersettings",
            name="garmin_calorie_adjustment",
            field=models.IntegerField(
                default=0,
                help_text="Calories to add to or subtract from Garmin daily expenditure",
            ),
        ),
    ]
