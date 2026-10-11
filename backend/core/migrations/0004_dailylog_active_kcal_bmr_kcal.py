from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0003_add_use_auto_thresholds'),
    ]

    operations = [
        migrations.AddField(
            model_name='dailylog',
            name='active_kcal',
            field=models.IntegerField(blank=True, null=True, help_text='Active calories component of calories_out (Garmin activeKilocalories)'),
        ),
        migrations.AddField(
            model_name='dailylog',
            name='bmr_kcal',
            field=models.IntegerField(blank=True, null=True, help_text='Resting/BMR calories component of calories_out (Garmin bmrKilocalories)'),
        ),
    ]
