# Generated by Django 2.2.17 on 2020-11-23 15:04

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('billing', '0010_auto_20201123_1329'),
    ]

    operations = [
        migrations.AddField(
            model_name='accountbillingaddress',
            name='default',
            field=models.BooleanField(blank=True, default=False, null=True),
        ),
    ]
