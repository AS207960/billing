# Generated by Django 2.2.17 on 2020-11-24 22:33

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('billing', '0015_auto_20201124_1712'),
    ]

    operations = [
        migrations.AddField(
            model_name='chargestate',
            name='can_reject',
            field=models.BooleanField(blank=True, default=True, null=True),
        ),
    ]